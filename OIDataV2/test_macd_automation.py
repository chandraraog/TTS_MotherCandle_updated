import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import datetime
from engine import IST
from macd_automation import MacdAutomation

class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.job=SimpleNamespace(engine=SimpleNamespace(root=Path(self.temp.name),demo=False,get_config=lambda:{'market':{'start':'09:15','end':'15:30','holidays':[]}}),start=Mock(return_value=True))
        self.a=MacdAutomation(self.job)
        self.a.token='fake';self.a.chat='fake'
        self.two={'M':'Strong Bull','W':'Strong Bull','D':'Weakening','240':'Weakening','60':'Weakening','15':'Weakening','5':'Weakening'}
        self.three={**self.two,'D':'Strong Bull'}
    def tearDown(self): self.temp.cleanup()
    def test_transition(self):
        self.assertEqual(self.a.transition(self.two,self.three),'Strong Bull')
        self.assertIsNone(self.a.transition(self.three,self.three))
        self.assertIsNone(self.a.transition({**self.two,'W':'Weakening'},self.three))
        bear=lambda s:{k:('Strong Bear' if v=='Strong Bull' else 'Recovering') for k,v in s.items()}
        self.assertEqual(self.a.transition(bear(self.two),bear(self.three)),'Strong Bear')
    def test_alert_observations_cross_report_alignment_break(self):
        import macd_mtf_scanner as scanner
        observed=Mock()
        def state(client,symbol,tf,cache):
            return {'trend':'Weakening' if tf=='W' else 'Strong Bull','price':1500}
        with patch.object(scanner,'get_state',side_effect=state), patch.object(scanner,'OBSERVATION_CALLBACK',observed):
            scanner.scan_symbol(None,'TEST',['M','W','D','240','60'],1000)
        observed.assert_called_once()
        self.assertEqual(observed.call_args.args[1],{tf: 'Weakening' if tf=='W' else 'Strong Bull' for tf in scanner.HTF_ORDER})
    def test_monthly_weak_only_and_lower_timeframes_strong(self):
        import macd_mtf_scanner as scanner
        for trend,macd,side in [('Recovering',-1,'Strong Bull'),('Weakening',1,'Strong Bear')]:
            def state(client,symbol,tf,cache):
                return {'trend':trend if tf=='M' else side,'price':1500,'macd':macd,'signal':macd-1}
            with patch.object(scanner,'get_state',side_effect=state),patch.object(scanner,'OBSERVATION_CALLBACK',None):
                result=scanner.scan_symbol(None,'TEST',['M','W','D'])
                self.assertEqual(result['momentum'],'weak')
                self.assertEqual(result['chain'],['M','W','D'])
            with patch.object(scanner,'get_state',return_value={'trend':trend,'price':1500,'macd':macd}),patch.object(scanner,'OBSERVATION_CALLBACK',None):
                self.assertEqual(scanner.scan_symbol(None,'TEST',['M','W','D'])['chain'],['M'])
    def test_additional_timeframe_alert_after_first_transition(self):
        self.a.configure({'hourly':False,'alerts':True})
        with patch('macd_automation.requests.post') as post:
            post.return_value.ok=True;post.return_value.json.return_value={'ok':True}
            for states in (self.two,self.three,{**self.three,'60':'Strong Bull'},{**self.three,'60':'Strong Bull'}):
                self.a.observe('TEST',states,1500)
            self.assertEqual(post.call_count,2)
    def test_weak_monthly_alert_and_no_mixed_direction(self):
        self.assertEqual(self.a.transition({**self.two,'M':'Recovering'},{**self.three,'M':'Recovering'}),'Strong Bull')
        self.assertIsNone(self.a.transition({**self.two,'M':'Strong Bear'},self.three))
    def test_daily_history_stays_within_standard_plan(self):
        import macd_mtf_scanner as scanner
        client=Mock()
        client.history.return_value={'s':'ok','candles':[]}
        with patch.object(scanner.time,'sleep'):
            scanner.fetch_candles(client,'NSE:NIFTY50-INDEX','D',scanner.LOOKBACK_DAYS['D'])
        calls=[call.kwargs['data'] for call in client.history.call_args_list]
        start=datetime.fromisoformat(calls[0]['range_from'])
        end=datetime.fromisoformat(calls[-1]['range_to'])
        self.assertLessEqual((end-start).days,365*5)
        for request in calls:
            self.assertLessEqual((datetime.fromisoformat(request['range_to'])-datetime.fromisoformat(request['range_from'])).days,360)
    def test_baseline_daily_dedup_and_restart(self):
        self.a.configure({'hourly':True,'alerts':True})
        with patch('macd_automation.requests.post') as post:
            post.return_value.ok=True;post.return_value.json.return_value={'ok':True}
            self.a.observe('NSE:TEST-EQ',self.two,1500)
            post.assert_not_called()
            self.a.observe('NSE:TEST-EQ',self.three,1500)
            self.a.observe('NSE:TEST-EQ',self.two,1500)
            other=MacdAutomation(self.job);other.token='fake';other.chat='fake'
            other.observe('NSE:TEST-EQ',self.three,1500)
            self.assertEqual(post.call_count,1)
    def test_disabled_and_incomplete(self):
        with patch('macd_automation.requests.post') as post:
            self.a.observe('TEST',self.two,1500)
            self.a.observe('TEST',{**self.three,'60':None},1500)
            self.a.observe('TEST',self.three,1500)
            post.assert_not_called()
    def test_new_ist_day_allows_new_transition(self):
        self.a.configure({'hourly':True,'alerts':True})
        with patch('macd_automation.requests.post') as post, patch('macd_automation.now_ist') as clock:
            post.return_value.ok=True;post.return_value.json.return_value={'ok':True}
            clock.return_value=datetime(2026,11,9,15,0,tzinfo=IST)
            self.a.observe('TEST',self.two,1500)
            self.a.observe('TEST',self.three,1500)
            clock.return_value=datetime(2026,11,10,10,0,tzinfo=IST)
            self.a.observe('TEST',self.two,1500)
            self.a.observe('TEST',self.three,1500)
            self.assertEqual(post.call_count,2)
    def test_uncertain_delivery_not_retried(self):
        import requests
        self.a.configure({'hourly':True,'alerts':True})
        with patch('macd_automation.requests.post',side_effect=requests.Timeout) as post:
            for states in (self.two,self.three,self.two,self.three):
                self.a.observe('TEST',states,1500)
            self.assertEqual(post.call_count,1)
            self.assertIn('uncertain',self.a.status()['error'])
    def test_schedule_session_and_interval(self):
        t=datetime(2026,11,9,10,0,tzinfo=IST)
        self.assertFalse(self.a.tick(t.replace(hour=20)))
        self.assertTrue(self.a.tick(t))
        with patch('macd_automation.now_ist',return_value=t): self.a.started('watchlist')
        self.assertFalse(self.a.tick(t.replace(minute=59)))
        self.assertTrue(self.a.tick(t.replace(hour=11)))
        self.a.configure({'hourly':False,'alerts':False})
        self.assertFalse(self.a.tick(t.replace(hour=12)))
