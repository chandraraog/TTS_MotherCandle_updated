import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from money_alerts import MoneyAlerts

class MoneyAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.alert=MoneyAlerts(self.root)
        self.creds=patch.object(MoneyAlerts,'credentials',return_value=('fake','fake'));self.creds.start()
        self.alert.configure({'enabled':True,'threshold':20,'near':3,'step':100})
        self.payload={'symbol':'NSE:TEST-EQ','spot':1000,'expiry':{'expiry':'123','date':'test'},'collected_at':'2026-11-10T10:00:00+05:30','rows':[],'errors':[]}
        for strike in range(650,1351,50):
            for side in ('CE','PE'):
                self.payload['rows'].append({'symbol':f'TEST{strike}{side}','strike':strike,'type':side,'money':1})
    def tearDown(self):
        self.creds.stop();self.temp.cleanup()
    def test_threshold_anchor_not_previous_reading(self):
        old={'CE':1,'PE':2,'view':'Bullish (PE leads)'}
        current={'CE':1.19,'PE':2,'view':'Bullish (PE leads)'}
        self.assertEqual(MoneyAlerts.changes(old,current,20)[0],[])
        reasons,anchor=MoneyAlerts.changes(old,{**current,'CE':1.2},20)
        self.assertTrue(reasons);self.assertEqual(anchor['CE'],1.2);self.assertEqual(anchor['PE'],2)
        self.assertEqual(MoneyAlerts.changes(anchor,{**current,'CE':1.2},20)[0],[])
        self.assertTrue(MoneyAlerts.changes(old,{**current,'CE':.8},20)[0])
    def test_negative_crossing_and_recovery(self):
        old={'CE':.01,'PE':2,'view':'Bullish (PE leads)'}
        reasons,anchor=MoneyAlerts.changes(old,{'CE':-.01,'PE':2},20)
        self.assertIn('turned negative',reasons[0])
        self.assertEqual(MoneyAlerts.changes(anchor,{'CE':-.01,'PE':2},20)[0],[])
        self.assertIn('returned non-negative',MoneyAlerts.changes(anchor,{'CE':0,'PE':2},20)[0][0])
    def test_side_change_margin(self):
        old={'CE':2,'PE':1,'view':'Bearish (CE leads)'}
        reasons,anchor=MoneyAlerts.changes(old,{'CE':1,'PE':2},20)
        self.assertTrue(any('View changed' in reason for reason in reasons))
        self.assertEqual(anchor['view'],'Bullish (PE leads)')
        self.assertEqual(MoneyAlerts.leader(1,1.1,20),'Neutral')
    def test_exact_step_window(self):
        with patch.object(self.alert,'deliver') as deliver:
            self.alert.observe(self.payload,{'oi_multiplier':1})
            self.assertEqual(deliver.call_args.args[2]['CE'],7)
            self.assertIn('700–1300',deliver.call_args.args[1])
        self.alert.configure({'enabled':True,'threshold':20,'near':3,'step':50})
        with patch.object(self.alert,'deliver') as deliver:
            self.alert.observe(self.payload,{'oi_multiplier':1})
            self.assertEqual(deliver.call_args.args[2]['CE'],7)
            self.assertIn('850–1150',deliver.call_args.args[1])
    def test_incomplete_window_skips(self):
        self.payload['rows']=[r for r in self.payload['rows'] if r['strike']!=700]
        with patch.object(self.alert,'deliver') as deliver:
            self.alert.observe(self.payload,{})
            deliver.assert_not_called()
    def test_disabled_and_demo_never_send(self):
        self.alert.configure({'enabled':False,'threshold':20,'near':3,'step':100})
        with patch('money_alerts.requests.post') as post:
            self.alert.observe(self.payload,{})
            self.alert.demo=True
            self.alert.settings['enabled']=True
            self.alert.observe(self.payload,{})
            post.assert_not_called()
    def test_restart_dedup_and_new_threshold_alert(self):
        with patch('money_alerts.requests.post') as post:
            post.return_value.ok=True;post.return_value.json.return_value={'ok':True}
            self.alert.observe(self.payload,{})
            MoneyAlerts(self.root).observe(self.payload,{})
            self.assertEqual(post.call_count,1)
            for row in self.payload['rows']:
                if row['type']=='CE':row['money']=1.2
            self.alert.observe(self.payload,{})
            self.assertEqual(post.call_count,2)
            self.alert.observe(self.payload,{})
            self.assertEqual(post.call_count,2)
    def test_uncertain_delivery_never_retries_same_reading(self):
        import requests
        with patch('money_alerts.requests.post',side_effect=requests.Timeout) as post:
            self.alert.observe(self.payload,{})
            MoneyAlerts(self.root).observe(self.payload,{})
            self.assertEqual(post.call_count,1)
            self.assertIn('uncertain',self.alert.status()['last_delivery']['state'])
    def test_collector_expands_window_before_depth_requests(self):
        from collectors.fyers_data import Fyers
        f=Fyers(self.root)
        def chain(symbol,count,expiry):
            low,high=(800,1200) if count==3 else (650,1350)
            return {'resolved_symbol':symbol,'expiryData':[{'expiry':'123','date':'test'}],
                    'optionsChain':[{'symbol':symbol,'ltp':1000}]+[
                        {'symbol':f'TEST{x}{side}','strike_price':x,'option_type':side}
                        for x in range(low,high+1,50) for side in ('CE','PE')]}
        with patch.object(f,'chain',side_effect=chain) as fetch, patch.object(f,'depth',return_value={'oiflag':True,'oi':200,'pdoi':100,'atp':10}):
            result=f.collect_money({'symbol':'NSE:TEST-EQ','strikes':3,'expiry':'','oi_multiplier':1,'alert_window':{'near':3,'step':100}})
        self.assertEqual(fetch.call_count,2)
        self.assertGreater(fetch.call_args.args[1],3)
        self.assertIn(700,{row['strike'] for row in result['rows']})

    def test_manual_report_top_selection_and_persistent_dedup(self):
        options={'threshold':20,'near':3,'step':100,'top':3}
        with patch('money_alerts.requests.post') as post:
            post.return_value.ok=True;post.return_value.json.return_value={'ok':True}
            result=self.alert.send_now(self.payload,options)
            self.assertTrue(result['sent'])
            text=post.call_args.kwargs['data']['caption']
            self.assertIn('CE ₹1L | PE ₹1L',text)
            self.assertIn('ATM view: Neutral · Gap 0%',text)
            self.assertEqual(post.call_args.kwargs['data']['show_caption_above_media'],'false')
            self.assertEqual(post.call_args.kwargs['data']['parse_mode'],'HTML')
            self.assertTrue(post.call_args.args[0].endswith('/sendPhoto'))
            self.assertTrue(post.call_args.kwargs['files']['photo'][1].startswith(b'\x89PNG'))
            self.assertEqual(len(text.splitlines()),4)
            self.assertFalse(MoneyAlerts(self.root).send_now(self.payload,options)['sent'])
            self.assertEqual(post.call_count,1)
        with self.alert.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM anchors').fetchone()[0],0)

    def test_manual_report_all_and_missing_window(self):
        options={'threshold':20,'near':3,'step':50,'top':0}
        with patch('money_alerts.requests.post') as post:
            post.return_value.ok=True;post.return_value.json.return_value={'ok':True}
            result=self.alert.send_now(self.payload,options)
            self.assertIn('7 high-money strikes',result['message'])
            self.payload['rows']=[r for r in self.payload['rows'] if r['strike']!=850]
            result=self.alert.send_now(self.payload,options)
            self.assertIn('Partial window',result['message'])
            self.assertTrue(post.call_args.kwargs['files']['photo'][1].startswith(b'\x89PNG'))
            self.assertIn('ATM 1,000:',post.call_args.kwargs['data']['caption'])
            self.assertEqual(post.call_count,2)

    def test_manual_demo_and_invalid_options_never_send(self):
        options={'threshold':20,'near':3,'step':100,'top':3}
        with patch('money_alerts.requests.post') as post:
            self.alert.demo=True
            with self.assertRaisesRegex(ValueError,'demo'):self.alert.send_now(self.payload,options)
            self.alert.demo=False
            with self.assertRaises(ValueError):self.alert.send_now(self.payload,{**options,'top':-1})
            post.assert_not_called()

    def test_manual_timeout_not_retried(self):
        import requests
        options={'threshold':20,'near':3,'step':100,'top':3}
        with patch('money_alerts.requests.post',side_effect=requests.Timeout) as post:
            with self.assertRaisesRegex(ValueError,'uncertain'):self.alert.send_now(self.payload,options)
            with self.assertRaisesRegex(ValueError,'already attempted'):self.alert.send_now(self.payload,options)
            self.assertEqual(post.call_count,1)

    def test_invalid_settings(self):
        for values in ({'threshold':0},{'threshold':float('nan')},{'near':4},{'step':0}):
            with self.assertRaises(ValueError):self.alert.configure({'enabled':False,'threshold':20,'near':3,'step':100,**values})
