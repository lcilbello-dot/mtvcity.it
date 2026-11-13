"""Validate the generated JSON independently before committing or serving it."""
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
import json,re

ROOT=Path(__file__).resolve().parent
def validate(feed):
    nearby = feed.get('nearbyEvents', [])
    assert feed['schemaVersion']==1 and len(feed['events'])<=1000 and len(nearby)<=1000
    config = json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
    allowed = {'AUTO-'+source['id']: source for source in config['sources']}
    seen=set()
    sources={s['id'] for s in feed['sources']+feed.get('nearbySources', [])}
    for source in feed['sources'] + feed.get('nearbySources', []):
        assert source['id'] in allowed and source['url'] == allowed[source['id']]['page']
    for event in feed['events'] + nearby:
        assert event['id'] not in seen;seen.add(event['id'])
        assert re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}',event['id'])
        assert event['type']=='event'
        assert date.fromisoformat(event['endDate'])>=date.fromisoformat(event['startDate'])
        assert event['sources'] and set(event['sources'])<=sources
        source = allowed['AUTO-'+event['automation']['sourceId']]
        url=urlsplit(event['officialUrl']);assert url.scheme=='https' and url.hostname in source['hosts'] and not url.username and not url.password and url.port in (None, 443)
        cities = source.get('municipalities', [source.get('municipality')])
        assert event.get('municipality') in cities
        assert event['routes']=={'it':'/it/eventi/'+event['id']+'/','en':'/en/events/'+event['id']+'/'}
        for field in ['name','title','metaDescription','description','dayPart','registration','audienceNote']:
            assert all(isinstance(event[field][lang],str) and 0<len(event[field][lang])<=20000 for lang in ['it','en'])
        if event['publication']=='published':
            assert event['status']=='reviewed' and event['eventStatus']=='scheduled'
            assert event['meetingPoint'] or event.get('contact')
        else:assert event['publication'] in ['draft','archive']
        assert not event['coordinateVerified'] and not event['meetingPointVerified']
    assert not any(e.get('municipality') for e in feed['events'])
    assert all(e.get('municipality') for e in nearby)
    return len(seen)

if __name__=='__main__':
    feed=json.loads((ROOT/'events-feed.json').read_text(encoding='utf-8'))
    print('Catalogo automatico verificato:',validate(feed),'eventi')
