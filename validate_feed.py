"""Validate the generated JSON independently before committing or serving it."""
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
import json,re

ROOT=Path(__file__).resolve().parent
def validate(feed):
    assert feed['schemaVersion']==1 and len(feed['events'])<=1000
    seen=set();sources={s['id'] for s in feed['sources']}
    for event in feed['events']:
        assert event['id'] not in seen;seen.add(event['id'])
        assert re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}',event['id'])
        assert event['type']=='event'
        assert date.fromisoformat(event['endDate'])>=date.fromisoformat(event['startDate'])
        assert event['sources'] and set(event['sources'])<=sources
        url=urlsplit(event['officialUrl']);assert url.scheme=='https' and url.hostname in ['www.promontevecchia.it','www.parcocurone.it','www.comune.montevecchia.lc.it'] and not url.username
        assert event['routes']=={'it':'/it/eventi/'+event['id']+'/','en':'/en/events/'+event['id']+'/'}
        for field in ['name','title','metaDescription','description','dayPart','registration','audienceNote']:
            assert all(isinstance(event[field][lang],str) and 0<len(event[field][lang])<=20000 for lang in ['it','en'])
        if event['publication']=='published':
            assert event['status']=='reviewed' and event['eventStatus']=='scheduled'
            assert event['meetingPoint'] or event.get('contact')
        else:assert event['publication'] in ['draft','archive']
        assert not event['coordinateVerified'] and not event['meetingPointVerified']
    return len(seen)

if __name__=='__main__':
    feed=json.loads((ROOT/'events-feed.json').read_text(encoding='utf-8'))
    print('Catalogo automatico verificato:',validate(feed),'eventi')
