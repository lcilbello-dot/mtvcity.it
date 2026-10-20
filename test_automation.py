import sys,unittest,copy
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parent))
from monitor_events import ical_events,entity,reconcile,public_fingerprint,checked_url,opencity_events,alert_fingerprint
from validate_feed import validate

SOURCE={'id':'proloco','owner':'ProMontevecchia','hosts':['calendar.google.com','www.promontevecchia.it'],'page':'https://www.promontevecchia.it/','area':'Montevecchia'}
def calendar(*blocks):return ('BEGIN:VCALENDAR\r\n'+''.join('BEGIN:VEVENT\r\n'+b.replace('\n','\r\n')+'\r\nEND:VEVENT\r\n' for b in blocks)+'END:VCALENDAR').encode()
BASE='UID:a\nDTSTART;TZID=Europe/Rome:20261020T180000\nDTEND;TZID=Europe/Rome:20261020T190000\nSUMMARY:Coro Pop\nLOCATION:Montevecchia'
TODAY=date(2026,10,19)

class Calendars(unittest.TestCase):
    def read(self,*blocks):return ical_events(calendar(*blocks),SOURCE,TODAY)
    def test_weekly_dst_and_until(self):
        rows,issues=self.read(BASE+'\nRRULE:FREQ=WEEKLY;UNTIL=20261103T225959Z')
        self.assertEqual(issues,[]);self.assertEqual([r['startDate'] for r in rows],['2026-10-20','2026-10-27','2026-11-03'])
        self.assertEqual({r['startTime'] for r in rows},{'18:00'})
    def test_exdate(self):
        rows,_=self.read(BASE+'\nRRULE:FREQ=WEEKLY;COUNT=3\nEXDATE;TZID=Europe/Rome:20261027T180000')
        self.assertEqual([r['startDate'] for r in rows],['2026-10-20','2026-11-03'])
    def test_moved_override(self):
        rows,_=self.read(BASE+'\nRRULE:FREQ=WEEKLY;COUNT=2',BASE.replace('20261020','20261028')+'\nRECURRENCE-ID;TZID=Europe/Rome:20261027T180000')
        self.assertEqual([r['startDate'] for r in rows],['2026-10-20','2026-10-28'])
        self.assertIn('2026-10-27',rows[1]['identity'])
    def test_cancelled_override_without_start(self):
        rows,_=self.read(BASE+'\nRRULE:FREQ=WEEKLY;COUNT=2','UID:a\nRECURRENCE-ID;TZID=Europe/Rome:20261027T180000\nSTATUS:CANCELLED')
        self.assertEqual(rows[1]['startDate'],'2026-10-27');self.assertEqual(rows[1]['eventStatus'],'cancelled')
    def test_all_day_exclusive_end(self):
        rows,issues=self.read('UID:b\nDTSTART;VALUE=DATE:20261024\nDTEND;VALUE=DATE:20261026\nSUMMARY:Festa\nLOCATION:Montevecchia')
        self.assertEqual(issues,[]);self.assertEqual(rows[0]['endDate'],'2026-10-25');self.assertIsNone(rows[0]['startTime'])
    def test_all_day_until(self):
        rows,issues=self.read('UID:b\nDTSTART;VALUE=DATE:20261020\nRRULE:FREQ=WEEKLY;UNTIL=20261027\nSUMMARY:Festa\nLOCATION:Montevecchia')
        self.assertEqual(issues,[]);self.assertEqual(len(rows),2)
    def test_postponed_is_not_upcoming(self):
        rows,_=self.read(BASE.replace('Coro Pop','RIMANDATO: Coro Pop'))
        self.assertEqual(entity(rows[0],SOURCE,TODAY)['publication'],'archive')
        self.assertEqual(rows[0]['eventStatus'],'postponed')
    def test_missing_place_stays_draft(self):
        rows,_=self.read(BASE.replace('\nLOCATION:Montevecchia',''))
        self.assertEqual(entity(rows[0],SOURCE,TODAY)['publication'],'draft')
    def test_monthly_only_declared_dates(self):
        rows,_=self.read(BASE+'\nRRULE:FREQ=MONTHLY;COUNT=3')
        self.assertEqual([r['startDate'] for r in rows],['2026-10-20','2026-11-20','2026-12-20'])
    def test_high_frequency_rejected(self):
        rows,issues=self.read(BASE+'\nRRULE:FREQ=SECONDLY')
        self.assertEqual(rows,[]);self.assertTrue(issues)
    def test_incomplete_calendar_rejected(self):
        with self.assertRaises(ValueError):ical_events(b'BEGIN:VCALENDAR',SOURCE,TODAY)
    def test_unhealthy_source_does_not_withdraw(self):
        row=entity(self.read(BASE)[0][0],SOURCE,TODAY)
        self.assertEqual(reconcile([row],[],set(),TODAY)[0]['publication'],'published')
        removed=reconcile([row],[],{'proloco'},TODAY)[0]
        self.assertEqual(removed['eventStatus'],'withdrawn')
    def test_unchanged_check_does_not_notify(self):
        row=entity(self.read(BASE)[0][0],SOURCE,TODAY);a={'events':[row],'sourceChecks':[]};b=copy.deepcopy(a)
        b['events'][0]['verified']='2026-10-20';b['events'][0]['automation']['lastSeen']='2026-10-20'
        self.assertEqual(public_fingerprint(a),public_fingerprint(b))
    def test_reordered_source_warnings_do_not_notify(self):
        a=[{'id':'proloco','issues':['Luogo mancante B','Luogo mancante A'],'ok':True},{'id':'parco','issues':[],'ok':True}]
        b=[copy.deepcopy(a[1]),copy.deepcopy(a[0])];b[1]['issues'].reverse()
        self.assertEqual(alert_fingerprint(a),alert_fingerprint(b))
        self.assertEqual(public_fingerprint({'events':[],'sourceChecks':a}),public_fingerprint({'events':[],'sourceChecks':b}))
    def test_new_warning_occurrence_is_significant(self):
        a=[{'id':'proloco','issues':['Luogo mancante'],'ok':True}];b=copy.deepcopy(a)
        b[0]['issues'].append('Luogo mancante')
        self.assertNotEqual(alert_fingerprint(a),alert_fingerprint(b))
    def test_foreign_redirect_rejected(self):
        for url in ['http://www.promontevecchia.it/','https://127.0.0.1/','https://x@www.promontevecchia.it/','https://www.promontevecchia.it.evil.test/']:
            with self.assertRaises(ValueError):checked_url(url,SOURCE['hosts'])
    def test_script_payload_remains_plain(self):
        rows,_=self.read(BASE.replace('Coro Pop','<script>alert(1)</script>Festa'))
        self.assertNotIn('<script>',rows[0]['name'])
    def test_generated_feed_validates(self):
        row=entity(self.read(BASE)[0][0],SOURCE,TODAY)
        self.assertEqual(validate({'schemaVersion':1,'sources':[{'id':'AUTO-proloco'}],'events':[row]}),1)
    def test_removed_incomplete_event_stays_private(self):
        row=entity(self.read(BASE.replace('\nLOCATION:Montevecchia',''))[0][0],SOURCE,TODAY)
        self.assertEqual(reconcile([row],[],set(),date(2026,10,25))[0]['publication'],'draft')
    def test_quoted_pdf_url(self):
        self.assertEqual(checked_url('https://www.promontevecchia.it/file name.pdf',SOURCE['hosts']),'https://www.promontevecchia.it/file%20name.pdf')
    def test_municipal_date_is_event_date(self):
        source={**SOURCE,'id':'comune','page':'https://www.comune.montevecchia.lc.it/Vivere-il-comune/Eventi','hosts':['www.comune.montevecchia.lc.it']}
        doc={'searchHits':[{'metadata':{'id':42,'published':'2026-01-01'},'extradata':{'ita-IT':{'urlAlias':'/Vivere-il-comune/Eventi/Festa'}},'data':{'ita-IT':{'event_title':'Festa','time_interval':{'recurrences':[{'id':'a','start':'2026-10-25T14:00:00+01:00','end':'2026-10-25T18:00:00+01:00'}]},'takes_place_in':[{'name':{'ita-IT':'Piazza'}}]}}}]}
        rows,issues=opencity_events(doc,source,TODAY)
        self.assertEqual(issues,[]);self.assertEqual(rows[0]['startDate'],'2026-10-25');self.assertEqual(rows[0]['startTime'],'14:00')
    def municipal_range(self,start,end):
        source={**SOURCE,'id':'comune','page':'https://www.comune.montevecchia.lc.it/Vivere-il-comune/Eventi','hosts':['www.comune.montevecchia.lc.it']}
        doc={'searchHits':[{'metadata':{'id':42},'extradata':{'ita-IT':{'urlAlias':'/Vivere-il-comune/Eventi/Raccolta'}},'data':{'ita-IT':{'event_title':'Raccolta','time_interval':{'recurrences':[{'id':'period','start':start,'end':end}]},'takes_place_in':[{'name':{'ita-IT':'Municipio'}}]}}}]}
        rows,issues=opencity_events(doc,source,TODAY)
        self.assertEqual(issues,[])
        return rows[0],source
    def test_municipal_period_includes_last_date_across_dst(self):
        row,source=self.municipal_range('2026-10-05T00:00:00+02:00','2026-10-31T00:00:00+01:00')
        self.assertEqual(row['startDate'],'2026-10-05');self.assertEqual(row['endDate'],'2026-10-31')
        self.assertIsNone(row['startTime'])
        self.assertEqual(entity(row,source,TODAY)['dayPart']['en'],'Times on the official programme')
    def test_municipal_single_day_preserves_declared_midnight(self):
        row,_=self.municipal_range('2026-10-25T00:00:00+02:00','2026-10-25T01:00:00+02:00')
        self.assertEqual(row['endDate'],'2026-10-25');self.assertEqual(row['startTime'],'00:00')
    def test_cancelled_master_without_date_is_tombstone(self):
        rows,issues=self.read('UID:a\nSTATUS:CANCELLED')
        self.assertEqual(rows,[]);self.assertEqual(issues,[])

if __name__=='__main__':unittest.main()
