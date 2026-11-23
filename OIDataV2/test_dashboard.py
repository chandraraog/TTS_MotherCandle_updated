import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pandas as pd

from calculations import money, bias, interpretation, net_view
from collectors.fyers_data import Fyers
from collectors.nse_daily import analyse
from engine import Engine, add_changes, validate, in_session, IST
from server import make_handler, ROOT
from storage import Store
from request_budget import RequestBudget
from macd_job import MacdJob


class CalculationTests(unittest.TestCase):
    def test_money_signed_and_units(self):
        self.assertEqual(money(120000, 100000, 50), 10)
        self.assertEqual(money(80000, 100000, 50), -10)
        self.assertEqual(money(120, 100, 50, 1000), 10)

    def test_missing_and_invalid_fields(self):
        for args in ((None,100,10), (100,None,10), (100,10,None), (100,10,0), (100,10,float('nan'))):
            self.assertIsNone(money(*args))
        self.assertEqual(money(100,0,50), .05)

    def test_negative_bias_is_not_writing_signal(self):
        self.assertEqual(bias(-100,-50)['view'], 'Unwinding / mixed')
        self.assertEqual(bias(-100,50)['difference_pct'], 150)
        self.assertEqual(bias(100,200)['view'], 'Bullish')
        self.assertEqual(bias(100,110)['view'], 'Neutral')
        self.assertEqual(bias(0,0)['difference_pct'], 0)
        self.assertEqual(bias(None,100)['view'], 'Incomplete')

    def test_interpretation(self):
        self.assertEqual(interpretation('FUT',1,1)[1], 'Aggressive Bullish')
        self.assertEqual(interpretation('CE',-1,1)[1], 'Aggressive Bearish')
        self.assertEqual(interpretation('PE',-1,1)[1], 'Aggressive Bullish')
        self.assertEqual(net_view(['Aggressive Bullish','Bullish','Aggressive Bullish']), 'Strong Bullish')
        self.assertEqual(net_view(['Bullish','Bearish','Bullish']), 'Mixed')
        self.assertEqual(net_view(['Bullish']), 'Incomplete')

    def test_snapshot_matching_and_day_reset(self):
        before={'collected_at':'2026-11-09T04:00:00+00:00','rows':[{'symbol':'A','oi':10,'money':5}]}
        after={'collected_at':'2026-11-09T05:00:00+00:00','rows':[{'symbol':'A','oi':14,'money':3},{'symbol':'B','oi':20}]}
        add_changes(after,before,'money')
        self.assertEqual(after['rows'][0]['snapshot_oi_change'],4)
        self.assertEqual(after['rows'][0]['snapshot_money_change'],-2)
        self.assertIsNone(after['rows'][1]['snapshot_oi_change'])
        after['collected_at']='2026-11-10T05:00:00+00:00'
        add_changes(after,before,'money')
        self.assertIsNone(after['rows'][0]['snapshot_oi_change'])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        test_root = ROOT / 'data' / 'tests'
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=test_root)
        assert Path(self.temp.name).resolve().is_relative_to(test_root.resolve())
        self.fyers=Fyers(Path(self.temp.name))
        self.chain={'expiryData':[{'date':'10-11-2026','expiry':'123'}], 'optionsChain':[
            {'symbol':'NSE:NIFTY50-INDEX','ltp':25000},
            {'symbol':'CE1','option_type':'CE','strike_price':25000},
            {'symbol':'PE1','option_type':'PE','strike_price':25000}]}
        self.cfg={'symbol':'NSE:NIFTY50-INDEX','strikes':5,'expiry':'','oi_multiplier':1}

    def tearDown(self):
        self.temp.cleanup()

    def test_money_uses_depth_atp_and_previous_day_oi(self):
        with patch.object(self.fyers,'chain',return_value=self.chain), patch.object(self.fyers,'depth',return_value={'oiflag':True,'oi':120000,'pdoi':100000,'atp':50,'ltp':80}):
            result=self.fyers.collect_money(self.cfg)
        self.assertEqual(result['totals'],{'CE':10,'PE':10})
        self.assertEqual(result['rows'][0]['money'],10)

    def test_partial_data_does_not_become_zero(self):
        with patch.object(self.fyers,'chain',return_value=self.chain), patch.object(self.fyers,'depth',side_effect=[{'oiflag':True,'oi':120000,'pdoi':100000,'atp':50},{'oiflag':True,'oi':120000,'atp':50}]):
            result=self.fyers.collect_money(self.cfg)
        self.assertIsNone(result['totals']['PE'])
        self.assertEqual(result['bias']['view'],'Incomplete')
        self.assertEqual(len(result['errors']),1)

    def test_expired_selection_rejected(self):
        with patch.object(self.fyers,'chain',return_value=self.chain), self.assertRaises(RuntimeError):
            self.fyers.collect_money({**self.cfg,'expiry':'999'})

    def test_master_discovery(self):
        text='101,NIFTY FUT,11,65,0.1,,session,date,4070908800,NSE:NIFTY99JANFUT,10,11,1,NIFTY,1,-1,XX,1\n'
        cache=Path(self.temp.name)/'data'/'NSE_FO.csv'
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(text)
        (cache.parent / 'NSE_CM.csv').write_text('1,NIFTY,0,1,0.05,,,,,NSE:NIFTY50-INDEX\n')
        self.assertEqual(self.fyers.universe(),['NSE:NIFTY50-INDEX'])
        self.assertEqual(self.fyers.master()[0]['lot_size'],65)

    def test_daily_expiry_and_atm_selection(self):
        base={'TckrSymb':'TEST','XpryDt':'2026-11-26','ClsPric':101,'PrvsClsgPric':100,'OpnIntrst':1000,'ChngInOpnIntrst':100,'StrkPric':0,'OptnTp':''}
        frame=pd.DataFrame([{**base,'FinInstrmTp':'STF'},
            {**base,'FinInstrmTp':'STF','XpryDt':'2026-12-29','ClsPric':110},
            *[{**base,'FinInstrmTp':'STO','StrkPric':strike,'OptnTp':side,'ClsPric':20,'PrvsClsgPric':22} for side in ('CE','PE') for strike in (100,110)]])
        row=analyse(frame,datetime(2026,11,9).date())[0]
        self.assertEqual(row['Fut Close'],101)
        self.assertEqual(row['ATM'],100)
        self.assertEqual(row['Total CE OI'],2000)
        self.assertEqual(row['PCR'],1)
        self.assertEqual(row['CE View'],'Aggressive Bearish')
        self.assertEqual(row['PE View'],'Aggressive Bullish')


class EngineTests(unittest.TestCase):
    def setUp(self):
        test_root = ROOT / 'data' / 'tests'
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=test_root)
        assert Path(self.temp.name).resolve().is_relative_to(test_root.resolve())
        self.root=Path(self.temp.name)
        (self.root/'config.json').write_bytes((ROOT/'config.json').read_bytes())
        self.engine=Engine(self.root,demo=True)

    def tearDown(self):
        self.engine.stop.set()
        self.temp.cleanup()

    def test_invalid_config_does_not_write(self):
        cfg=self.engine.get_config()
        cfg['money']['interval_seconds']=1
        with self.assertRaises(ValueError): self.engine.update_config(cfg)
        self.assertFalse((self.root/'config.demo.json').exists())

    def test_money_refresh_off_still_allows_manual_collection(self):
        cfg=self.engine.get_config()
        cfg['money']['interval_seconds']=0
        self.engine.update_config(cfg)
        for forced in (False,True):
            self.engine.stop.clear()
            if forced: self.engine.forced.add('money')
            with patch.object(self.engine,'run_job',return_value=True) as run, patch.object(self.engine.wakes['money'],'wait',side_effect=lambda _:self.engine.stop.set()):
                self.engine.worker('money')
            self.assertEqual(run.call_count,int(forced))

    def test_money_accepts_bse_mcx_and_custom_symbols(self):
        for symbol in ('BSE:SENSEX-INDEX','MCX:NATURALGAS','MCX:NATURALGAS26NOVFUT','NSE:SBIN-EQ'):
            cfg=self.engine.get_config()
            cfg['money']['symbol']=symbol
            self.engine.update_config(cfg)
            self.assertEqual(self.engine.get_config()['money']['symbol'],symbol)

    def test_market_hours_holidays_weekend(self):
        cfg=self.engine.get_config()
        self.assertTrue(in_session(datetime(2026,11,9,10,tzinfo=IST),cfg))
        self.assertFalse(in_session(datetime(2026,11,9,18,tzinfo=IST),cfg))
        self.assertFalse(in_session(datetime(2026,11,8,10,tzinfo=IST),cfg))
        cfg['market']['holidays']=['2026-11-09']
        self.assertFalse(in_session(datetime(2026,11,9,10,tzinfo=IST),cfg))

    def test_demo_isolated_and_scope_changes(self):
        self.assertTrue(self.engine.run_job('money',self.engine.get_config()))
        self.assertIsNotNone(self.engine.snapshot('money'))
        self.assertFalse((self.root/'data'/'market_data.sqlite').exists())
        cfg=self.engine.get_config(); cfg['money']['symbol']='NSE:SBIN-EQ'
        self.engine.update_config(cfg)
        self.assertIsNone(self.engine.snapshot('money'))

    def test_failure_preserves_snapshot(self):
        cfg=self.engine.get_config()
        self.engine.run_job('money',cfg)
        original=self.engine.snapshot('money')['id']
        with patch('engine.demo_payload',side_effect=RuntimeError('Simulated outage')):
            self.assertFalse(self.engine.run_job('money',cfg))
        self.assertEqual(self.engine.snapshot('money')['id'],original)
        self.assertEqual(self.engine.status['money']['error'],'Simulated outage')

    def test_manual_refresh_is_deduplicated(self):
        self.assertTrue(self.engine.trigger('money'))
        self.assertFalse(self.engine.trigger('money'))
        self.engine.status['oi']['running']=True
        self.assertFalse(self.engine.trigger('oi'))

    def test_minute_and_daily_request_budgets(self):
        budget=self.engine.fyers.budget
        with patch('request_budget.time.time',return_value=1794200000):
            self.assertEqual(budget.reserve(1,2),0)
            self.assertGreater(budget.reserve(1,2),0)
        with patch('request_budget.time.time',return_value=1794200061):
            self.assertEqual(budget.reserve(1,2),0)
            with self.assertRaisesRegex(RuntimeError,'daily request budget'):
                budget.reserve(1,2)
        with patch('request_budget.time.time',return_value=1794286400):
            self.assertEqual(budget.reserve(1,2),0)

    def test_broker_cooldown_survives_restart(self):
        budget=self.engine.fyers.budget
        budget.cooldown()
        reloaded=RequestBudget(budget.path)
        with self.assertRaisesRegex(RuntimeError,'cooldown'):
            reloaded.reserve(40,4500)

    def test_nse_history_sorted_by_trading_date_not_insert_order(self):
        for day in ('2026-11-09','2026-11-06'):
            self.engine.store.save('nse',day,{'collected_at':'2026-11-09T10:00:00+00:00','trade_date':day})
        self.assertEqual(self.engine.store.latest('nse')['trade_date'],'2026-11-09')
        self.assertEqual(self.engine.store.history('nse')[0]['scope'],'2026-11-09')

    def test_macd_bhavcopy_resolves_exact_symbols_and_deduplicates(self):
        self.engine.store.save('nse','2026-11-09',{'collected_at':'2026-11-09T10:00:00+00:00',
            'trade_date':'2026-11-09','rows':[{'Symbol':'NIFTY'},{'Symbol':'SBIN'},{'Symbol':'SBIN'}]})
        contracts=[{'symbol':'NSE:NIFTY26NOVFUT','underlying_name':'NIFTY','underlying':'NSE:NIFTY50-INDEX'},
                   {'symbol':'NSE:SBIN26NOVFUT','underlying_name':'SBIN','underlying':'NSE:SBIN-EQ'}]
        with patch.object(self.engine.fyers,'master',return_value=contracts):
            symbols,metadata=MacdJob(self.engine).resolve_bhavcopy()
        self.assertEqual(symbols,['NSE:NIFTY50-INDEX','NSE:SBIN-EQ'])
        self.assertEqual(metadata['bhavcopy_date'],'2026-11-09')
        self.assertEqual(metadata['candle_source'],'FYERS')

    def test_macd_bhavcopy_requires_saved_daily_data(self):
        with self.assertRaisesRegex(ValueError,'No saved NSE bhavcopy'):
            MacdJob(self.engine).resolve_bhavcopy()

    def test_macd_sources_are_isolated_and_validated(self):
        job=MacdJob(self.engine)
        self.assertNotEqual(job.report_path('watchlist'),job.report_path('bhavcopy'))
        with self.assertRaises(ValueError): job.report_path('../../.env')
        self.engine.demo=False
        with patch('macd_job.threading.Thread'):
            self.assertTrue(job.start('bhavcopy'))
            self.assertFalse(job.start('watchlist'))
        self.assertEqual(job.status()['source'],'bhavcopy')
        self.assertEqual(MacdJob(self.engine).selected_source,'bhavcopy')

    def test_macd_demo_never_starts_broker_scan(self):
        job=MacdJob(self.engine)
        with self.assertRaisesRegex(ValueError,'demo'):
            job.start()
        self.assertFalse(job.status()['running'])

    def test_oi_and_bhavcopy_collect_only_when_requested(self):
        for job in ('oi','nse'):
            for forced in (False,True):
                self.engine.stop.clear()
                if forced:
                    self.engine.forced.add(job)
                with patch.object(self.engine,'run_job',return_value=True) as run, patch.object(self.engine.wakes[job],'wait',side_effect=lambda _:self.engine.stop.set()):
                    self.engine.worker(job)
                self.assertEqual(run.call_count,int(forced))
                self.assertIn('Manual only',self.engine.status[job]['next_run'])

    def test_money_saved_nearest_matches_resolved_expiry(self):
        cfg=self.engine.get_config()
        cfg['money']['expiry']=''
        scope=self.engine.scope('money',cfg)
        self.engine.store.save('money',scope,{'collected_at':'2026-11-09T10:00:00+00:00','expiry':{'expiry':'1794910200'},'rows':[]})
        selected={**cfg['money'],'expiry':'1794910200'}
        self.assertEqual(len(self.engine.store.money_history(selected)),1)
        self.assertEqual(self.engine.store.money_history({**selected,'expiry':'123'}),[])
        self.assertEqual(self.engine.store.money_history({**selected,'symbol':'NSE:SBIN-EQ'}),[])

    def test_macd_scan_price_default_validation_and_persistence(self):
        job=MacdJob(self.engine)
        self.assertEqual(job.status()['min_price'],1000)
        for value in (-1, float('nan'), float('inf'), 'bad', True):
            with self.assertRaises(ValueError): job.start('bhavcopy', value)
        self.engine.demo=False
        with patch('macd_job.threading.Thread'):
            self.assertTrue(job.start('bhavcopy',1500))
        self.assertEqual(job.status()['min_price'],1500)
        self.assertEqual(MacdJob(self.engine).min_price,1500)

    def test_macd_duplicate_run_is_blocked(self):
        self.engine.demo=False
        job=MacdJob(self.engine)
        with patch('macd_job.threading.Thread') as thread:
            self.assertTrue(job.start())
            self.assertFalse(job.start())
            thread.return_value.start.assert_called_once()

    def test_macd_failure_preserves_saved_report(self):
        report=self.root/'macd_scan_report.html'
        report.write_text('previous report')
        job=MacdJob(self.engine)
        job.run()  # temporary test root intentionally has no scanner module
        self.assertFalse(job.status()['running'])
        self.assertTrue(job.status()['error'])
        self.assertEqual(report.read_text(),'previous report')

    def test_http_routes_and_origin_protection(self):
        self.engine.run_job('money',self.engine.get_config())
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.engine))
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            for path in ('/money.html','/oi.html','/macd.html','/macd.js','/app.js','/style.css','/api/state','/api/macd/status','/api/history?job=money','/api/snapshot?job=money'):
                with urlopen(url+path) as response: self.assertEqual(response.status,200)
            with urlopen(url+'/api/snapshot?job=money') as response:
                self.assertEqual(len(json.load(response)['snapshot']['rows']),22)
            for path in ('/.env','/../config.json'):
                with self.assertRaises(HTTPError) as error: urlopen(url+path)
                self.assertEqual(error.exception.code,404)
            request=Request(url+'/api/refresh',data=b'{"job":"money"}',headers={'Content-Type':'application/json','Origin':'https://example.com'})
            with self.assertRaises(HTTPError) as error: urlopen(request)
            self.assertEqual(error.exception.code,403)
            request=Request(url+'/api/refresh',data=b'{"job":"money"}',headers={'Content-Type':'application/json'})
            with urlopen(request) as response: self.assertTrue(json.load(response)['queued'])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__=='__main__':
    unittest.main(verbosity=2)
