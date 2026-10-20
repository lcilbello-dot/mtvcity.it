"""Collect official public calendars; publish only explicit, parseable event facts.

No browser, account, LLM, arbitrary URL, photo or copied editorial description.
External text is data. The collector never follows instructions in a source.
"""
from __future__ import annotations
import argparse, copy, hashlib, html, io, json, os, re, sys, unicodedata
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote, quote, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo
from dateutil.rrule import rrulestr, rruleset
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parent
ROME=ZoneInfo('Europe/Rome')
MAX_BYTES=4*1024*1024
MONTHS={m:i+1 for i,m in enumerate('gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split())}
WEEKDAYS=['lunedì','martedì','mercoledì','giovedì','venerdì','sabato','domenica']

def plain(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]*>',' ',str(value or '')))).strip()

def key(value):
    value=unicodedata.normalize('NFKD',plain(value).lower())
    return re.sub(r'[^a-z0-9]','',re.sub(r'20\d\d','',value))

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else copy.deepcopy(default)

def write_json(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temp,path)

def checked_url(url, hosts):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in hosts or p.username or p.password or p.port not in (None,443):
        raise ValueError('URL fuori dalle fonti autorizzate')
    return urlunsplit((p.scheme,p.netloc,quote(unquote(p.path),safe='/'),p.query,''))

class Redirects(HTTPRedirectHandler):
    def __init__(self,hosts): self.hosts=hosts
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        checked_url(newurl,self.hosts)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url,source):
    url=checked_url(url,source['hosts'])
    request=Request(url,headers={'User-Agent':'MTVCityEvents/1.0 (+https://mtvcity.it; info@mtvcity.it)','Accept':'text/calendar,application/pdf,application/ld+json,text/html,application/json'})
    with build_opener(Redirects(source['hosts'])).open(request,timeout=25) as response:
        body=response.read(MAX_BYTES+1)
    if len(body)>MAX_BYTES:raise ValueError('Fonte troppo grande')
    return body

class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.links=[];self.jsonld=[];self.text=[];self.script=None;self.skip=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='a' and a.get('href'):self.links.append(a['href'])
        if tag=='script':self.script=[] if a.get('type')=='application/ld+json' else None
        if tag in ('script','style','nav','footer','header'):self.skip+=1
    def handle_endtag(self,tag):
        if tag=='script' and self.script is not None:
            try:self.jsonld.append(json.loads(''.join(self.script)))
            except ValueError:pass
            self.script=None
        if tag in ('script','style','nav','footer','header'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if self.script is not None:self.script.append(data)
        if not self.skip:self.text.append(data)

def parse_page(body):
    p=Page();p.feed(body.decode('utf-8',errors='replace'));return p

def ical_text(value):
    return plain(re.sub(r'\\([nN,;\\])',lambda m:'\n' if m[1].lower()=='n' else m[1],value))

def stamp(value,params=None):
    params=params or {}
    if params.get('VALUE')=='DATE' or re.fullmatch(r'\d{8}',value):
        return datetime.strptime(value,'%Y%m%d').replace(tzinfo=ROME),True
    tz=timezone.utc if value.endswith('Z') else ZoneInfo(params.get('TZID','Europe/Rome').strip('"'))
    return datetime.strptime(value.rstrip('Z'),'%Y%m%dT%H%M%S').replace(tzinfo=tz),False

def ical_blocks(body):
    text=body.decode('utf-8-sig')
    if 'BEGIN:VCALENDAR' not in text or 'END:VCALENDAR' not in text:raise ValueError('Calendario incompleto')
    lines=re.sub(r'\r?\n[ \t]','',text).splitlines();blocks=[];current=None;depth=0
    for line in lines:
        if line=='BEGIN:VEVENT':current=defaultdict(list);depth=0;continue
        if current is None:continue
        if line=='END:VEVENT':blocks.append(dict(current));current=None;continue
        if line.startswith('BEGIN:'):depth+=1;continue
        if line.startswith('END:'):depth-=1;continue
        if depth or ':' not in line:continue
        prop,value=line.split(':',1);parts=prop.split(';');params={}
        for part in parts[1:]:
            if '=' in part:k,v=part.split('=',1);params[k.upper()]=v
        current[parts[0].upper()].append((value,params))
    if len(blocks)>5000:raise ValueError('Troppe schede nel calendario')
    return blocks

def first(block,name,default=''):
    return block.get(name,[(default,{})])[0][0]

def ical_events(body,source,today,days=180):
    groups=defaultdict(list);events=[];issues=[]
    for block in ical_blocks(body):groups[first(block,'UID')].append(block)
    lower=datetime.combine(today-timedelta(days=14),time.min,ROME)
    upper=datetime.combine(today+timedelta(days=days),time.max,ROME)
    for uid,blocks in groups.items():
        if not uid:issues.append('Appuntamento senza UID');continue
        masters=[b for b in blocks if 'RECURRENCE-ID' not in b]
        if not masters:issues.append('Eccezione senza evento principale: '+uid[:70]);continue
        master=max(masters,key=lambda b:(int(first(b,'SEQUENCE','0')),first(b,'DTSTAMP')))
        if 'DTSTART' not in master:
            if first(master,'STATUS')=='CANCELLED':continue
            issues.append('Appuntamento senza data: '+uid[:70]);continue
        try:
            start,all_day=stamp(*master['DTSTART'][0])
            end=stamp(*master['DTEND'][0])[0] if 'DTEND' in master else start+timedelta(days=1 if all_day else 0)
            duration=end-start
            if duration<timedelta(0):raise ValueError('Fine precedente all’inizio')
            rule=rruleset();rule.rdate(start)
            for value,_ in master.get('RRULE',[]):
                if re.search(r'FREQ=(?:SECONDLY|MINUTELY|HOURLY)',value):raise ValueError('Ricorrenza troppo frequente')
                if all_day:
                    def date_until(match):
                        until=datetime.strptime(match[1],'%Y%m%d').replace(hour=23,minute=59,second=59,tzinfo=ROME)
                        return 'UNTIL='+until.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    value=re.sub(r'UNTIL=(\d{8})(?=;|$)',date_until,value)
                rule.rrule(rrulestr(value,dtstart=start))
            for value,params in master.get('RDATE',[]):
                for entry in value.split(','):rule.rdate(stamp(entry,params)[0])
            excluded=set()
            for value,params in master.get('EXDATE',[]):
                for entry in value.split(','):
                    dt=stamp(entry,params)[0];rule.exdate(dt);excluded.add(dt)
            overrides={}
            for block in blocks:
                if 'RECURRENCE-ID' not in block:continue
                if block['RECURRENCE-ID'][0][1].get('RANGE'):raise ValueError('Eccezione THISANDFUTURE da verificare')
                rid=stamp(*block['RECURRENCE-ID'][0])[0]
                old=overrides.get(rid)
                if old is None or (int(first(block,'SEQUENCE','0')),first(block,'DTSTAMP'))>=(int(first(old,'SEQUENCE','0')),first(old,'DTSTAMP')):overrides[rid]=block
            instances=[]
            for instance in rule.xafter(lower-duration,inc=True):
                if instance>upper:break
                instances.append(instance)
                if len(instances)>1000:raise ValueError('Troppe ricorrenze')
            for rid,block in overrides.items():
                if 'DTSTART' in block and lower<=stamp(*block['DTSTART'][0])[0]<=upper and rid not in instances:instances.append(rid)
            for rid in instances:
                block=dict(master);block.update(overrides.get(rid,{}))
                actual,day=stamp(*overrides[rid]['DTSTART'][0]) if rid in overrides and 'DTSTART' in overrides[rid] else (rid,all_day)
                finish=stamp(*block['DTEND'][0])[0] if rid in overrides and 'DTEND' in overrides[rid] else actual+duration
                if finish<lower or actual>upper:continue
                title=ical_text(first(block,'SUMMARY'))
                if not title or len(title)>240:raise ValueError('Titolo assente o troppo lungo')
                cancelled=first(block,'STATUS')=='CANCELLED' or bool(re.search(r'\bannullat[oa]\b|\bcancell?ed\b',title,re.I))
                postponed=bool(re.search(r'\brimandat[oa]\b|\brinviat[oa]\b|\bpostponed\b',title,re.I))
                local=actual.astimezone(ROME);last=finish.astimezone(ROME)
                if finish>actual:last-=timedelta(microseconds=1)
                events.append({'identity':uid+'|'+rid.isoformat(),'uid':uid,'name':title,'startDate':local.date().isoformat(),'endDate':last.date().isoformat(),'startTime':None if day else local.strftime('%H:%M'),'dayPart':'Intera giornata' if day else local.strftime('%H:%M'),'place':ical_text(first(block,'LOCATION')),'officialUrl':source['page'],'eventStatus':'cancelled' if cancelled else ('postponed' if postponed else 'scheduled'),'calendar':True})
        except (ValueError,KeyError,OverflowError) as error:
            issues.append(uid[:60]+': '+str(error))
    return events,issues

def parco_events(body,source,today,document):
    page=parse_page(body)
    links=[urljoin(source['url'],link) for link in page.links if re.search(r'locandina.*visite.*guidate.*20\d\d\.pdf',unquote(link),re.I)]
    if len(set(links))!=1:raise ValueError('Calendario PDF del Parco non identificabile univocamente')
    pdf_url=checked_url(links[0],source['hosts'])
    pdf=document(pdf_url)
    reader=PdfReader(io.BytesIO(pdf))
    if len(reader.pages)>6:raise ValueError('Struttura del calendario PDF cambiata')
    text='\n'.join(p.extract_text(extraction_mode='layout') or '' for p in reader.pages)
    year_match=re.search(r'20\d\d',unquote(pdf_url));year=int(year_match[0])
    # The published PDF has one header line per visit. Never extract dates from prose.
    headers=re.findall(r'(?im)^\s*(Lunedì|Martedì|Mercoledì|Giovedì|Venerdì|Sabato|Domenica)\s+(\d{1,2})\s+('+ '|'.join(MONTHS)+r')\s+[^/\n]+/\s*(Mattina|Notturna|Tutta\s+la\s+giornata)\s+[–—-]\s+([^\n]+)',text)
    if not headers:raise ValueError('Intestazioni del calendario PDF non riconosciute')
    events=[];issues=[]
    for weekday,day,month,part,title in headers:
        dt=date(year,MONTHS[month.lower()],int(day));title=plain(title)
        if not today-timedelta(days=14)<=dt<=today+timedelta(days=180):continue
        if key(weekday)!=key(WEEKDAYS[dt.weekday()]):
            issues.append(f'{dt}: giorno della settimana discordante nel PDF');continue
        events.append({'identity':dt.isoformat()+'|'+key(title),'uid':dt.isoformat()+'|'+key(title),'name':title,'startDate':dt.isoformat(),'endDate':dt.isoformat(),'startTime':None,'dayPart':plain(part),'place':'','officialUrl':pdf_url,'eventStatus':'scheduled','booking':'gev@parcocurone.it','calendar':False})
    return events,issues,hashlib.sha256(pdf).hexdigest()

def walk_json(value):
    if isinstance(value,dict):
        yield value
        for item in value.values():yield from walk_json(item)
    elif isinstance(value,list):
        for item in value:yield from walk_json(item)

def jsonld_events(body,source,today):
    events=[];issues=[]
    for doc in parse_page(body).jsonld:
        for row in walk_json(doc):
            types=row.get('@type',[]);types=[types] if isinstance(types,str) else types
            if not any(t in ['Event','MusicEvent','SportsEvent','SocialEvent','EducationEvent'] for t in types):continue
            try:
                start=datetime.fromisoformat(row['startDate'].replace('Z','+00:00'));finish=datetime.fromisoformat(row.get('endDate',row['startDate']).replace('Z','+00:00'))
                if start.tzinfo:start=start.astimezone(ROME)
                if finish.tzinfo:finish=finish.astimezone(ROME)
                if finish<start:raise ValueError('Date discordanti')
                if not today-timedelta(days=14)<=start.date()<=today+timedelta(days=180):continue
                name=plain(row['name']);url=checked_url(row.get('url',source['page']),source['hosts'])
                location=row.get('location',{});place=plain(location.get('name','')) if isinstance(location,dict) else plain(location)
                status='cancelled' if 'EventCancelled' in row.get('eventStatus','') else 'scheduled'
                events.append({'identity':url+'|'+start.isoformat(),'uid':url,'name':name,'startDate':start.date().isoformat(),'endDate':finish.date().isoformat(),'startTime':start.strftime('%H:%M') if 'T' in row['startDate'] else None,'dayPart':start.strftime('%H:%M') if 'T' in row['startDate'] else 'Orario sul programma ufficiale','place':place,'officialUrl':url,'eventStatus':status,'calendar':True})
            except (ValueError,KeyError,TypeError) as error:issues.append('Dati strutturati incompleti: '+str(error))
    return events,issues

def opencity_events(doc,source,today):
    events=[];issues=[]
    for row in doc.get('searchHits',[]):
        metadata=row.get('metadata',{});data=row.get('data',{}).get('ita-IT',{});extra=row.get('extradata',{}).get('ita-IT',{})
        if 'privacy.private' in metadata.get('stateIdentifiers',[]):continue
        name=plain(data.get('event_title'));url=checked_url(urljoin(source['page'],extra.get('urlAlias','')),source['hosts'])
        dates=data.get('time_interval',{}).get('recurrences',[])
        if not dates:
            # Do not turn a news publication date into an event date.
            issues.append('Scheda comunale senza date strutturate: '+name[:80]);continue
        for occurrence in dates:
            try:
                start=datetime.fromisoformat(occurrence['start'].replace('Z','+00:00')).astimezone(ROME)
                end=datetime.fromisoformat(occurrence['end'].replace('Z','+00:00')).astimezone(ROME)
                if end<start:raise ValueError('Fine precedente all’inizio')
                if end.date()<today-timedelta(days=14) or start.date()>today+timedelta(days=180):continue
                place=', '.join(plain(p.get('name',{}).get('ita-IT','')) for p in data.get('takes_place_in',[]))
                organiser=', '.join(plain(p.get('name',{}).get('ita-IT','')) for p in data.get('organizer',[])) or plain(data.get('organizer_text'))
                status='cancelled' if re.search(r'\bannullat[oa]\b',name,re.I) else ('postponed' if re.search(r'\brinviat[oa]\b|\brimandat[oa]\b',name,re.I) else 'scheduled')
                # OpenCity's municipal dates are inclusive, unlike iCalendar DTEND.
                # Midnight on a multi-day listing is a date boundary, not an opening time.
                date_range=start.time()==end.time()==datetime.min.time() and end.date()>start.date()
                events.append({'identity':str(metadata['id'])+'|'+str(occurrence['id']),'uid':str(metadata['id']),'name':name,'startDate':start.date().isoformat(),'endDate':end.date().isoformat(),'startTime':None if date_range else start.strftime('%H:%M'),'dayPart':'Orari sul programma ufficiale' if date_range else start.strftime('%H:%M'),'place':place,'organiser':organiser,'officialUrl':url,'eventStatus':status,'calendar':not bool(organiser)})
            except (ValueError,KeyError,TypeError) as error:issues.append('Data comunale non valida: '+name[:60]+': '+str(error))
    return events,issues

def entity(raw,source,today):
    uid='auto-'+source['id']+'-'+hashlib.sha256(raw['identity'].encode()).hexdigest()[:20]
    name=raw['name'];place=raw['place'] or source['area'];owner=raw.get('organiser') or source['owner']
    booking=raw.get('booking')
    registration_it='Prenotazione via email a '+booking+'. Ritrovo e orario vengono comunicati alla conferma.' if booking else 'Consulta il programma ufficiale per partecipazione, prenotazioni, eventuali quote e aggiornamenti.'
    registration_en='Book by email at '+booking+'. The meeting point and time are provided on confirmation.' if booking else 'See the official programme for participation, bookings, any fees and updates.'
    english_part={'Mattina':'Morning','Notturna':'Evening / night','Tutta la giornata':'All day','Intera giornata':'All day','Orari sul programma ufficiale':'Times on the official programme'}.get(raw['dayPart'],raw['dayPart'])
    sid='AUTO-'+source['id']
    result={'id':uid,'type':'event','name':{'it':name,'en':name},'title':{'it':name+' | MTV City','en':name+' | MTV City'},'metaDescription':{'it':f'{name}: appuntamento del {raw["startDate"]}. Programma e aggiornamenti sulla fonte ufficiale.','en':f'{name}: event on {raw["startDate"]}. Programme and updates on the official source.'},'description':{'it':f'{raw["startDate"]}: {name}. Appuntamento presente nel programma di {owner}. {registration_it}','en':f'{raw["startDate"]}: {name}. An event listed by {owner}. The event title is shown in its original language. {registration_en}'},'dayPart':{'it':raw['dayPart'],'en':english_part},'registration':{'it':registration_it,'en':registration_en},'audienceNote':{'it':'Verifica con l’organizzatore requisiti, accessibilità e idoneità per bambini.','en':'Check requirements, accessibility and suitability for children with the organiser.'},'startDate':raw['startDate'],'endDate':raw['endDate'],'startTime':raw['startTime'],'organiser':owner,'officialUrl':raw['officialUrl'],'contact':booking,'meetingPoint':raw['place'] or None,'meetingPointVerified':False,'coordinates':None,'coordinateVerified':False,'audience':'general','categories':['natura'] if source['id']=='parco' else ['cultura'],'gallery':[],'visualIcon':'walk' if source['id']=='parco' else 'calendar','sources':[sid],'verified':today.isoformat(),'publication':'archive' if raw['endDate']<today.isoformat() or raw['eventStatus'] in ['cancelled','postponed'] else 'published','status':'reviewed','routes':{'it':'/it/eventi/'+uid+'/','en':'/en/events/'+uid+'/'},'eventStatus':raw['eventStatus'],'automation':{'sourceId':source['id'],'uid':raw['uid'],'identity':raw['identity'],'publisherOnly':raw['calendar'],'lastSeen':today.isoformat()}}
    if not raw['place'] and not booking and raw['eventStatus']=='scheduled':
        result['publication']='draft';result['status']='pending_automation_review';result['automation']['issue']='Luogo non dichiarato nella fonte'
    return result

def reconcile(previous, found, healthy, today):
    merged={e['id']:copy.deepcopy(e) for e in previous}
    for e in found:merged[e['id']]=e
    active_ids={e['id'] for e in found}
    for uid,e in merged.items():
        if uid not in active_ids and e['automation']['sourceId'] in healthy and e['startDate']>=today.isoformat():
            e['publication']='archive';e['eventStatus']='withdrawn'
        if e.get('status','').startswith('pending'):e['publication']='draft'
        elif e['endDate']<today.isoformat() and e['publication']=='published':e['publication']='archive'
    return sorted(merged.values(),key=lambda e:(e['startDate'],e['id']))

def alert_facts(checks):
    # Calendar providers may reorder unchanged VEVENT blocks on every request.
    # Retain repeated warnings (different occurrences), but ignore their order.
    return sorted([{'source':s['id'],'issues':sorted(s.get('issues',[])),'ok':s['ok']} for s in checks],key=lambda s:s['source'])

def alert_fingerprint(checks):
    return digest(alert_facts(checks))

def public_fingerprint(feed):
    return digest({'events':[{k:v for k,v in e.items() if k not in ['verified','automation']} for e in sorted(feed.get('events',[]),key=lambda e:e['id'])], 'issues':alert_facts(feed.get('sourceChecks',[]))})

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--fixtures',type=Path);parser.add_argument('--apply',action='store_true');parser.add_argument('--today',type=date.fromisoformat);parser.add_argument('--output',type=Path,default=ROOT/'run');args=parser.parse_args(argv)
    today=args.today or datetime.now(ROME).date();now=datetime.now(ROME).isoformat(timespec='seconds')
    config=read_json(ROOT/'sources.json');old=read_json(ROOT/'events-feed.json',{'events':[]})
    found=[];checks=[];healthy=set();all_sources=[];cancelled_uids={}
    seed=read_json(ROOT/'seed-events.json')['events']
    for source in config['sources']:
        check={'id':source['id'],'owner':source['owner'],'checkedAt':now,'ok':False,'issues':[]}
        try:
            body=(args.fixtures/source['fixture']).read_bytes() if args.fixtures else fetch(source['url'],source)
            check['sha256']=hashlib.sha256(body).hexdigest()
            if source['kind']=='ical':
                raw,issues=ical_events(body,source,today,config['daysAhead'])
                cancelled_uids[source['id']]={first(b,'UID') for b in ical_blocks(body) if first(b,'STATUS')=='CANCELLED' and 'RECURRENCE-ID' not in b and 'DTSTART' not in b}
            elif source['kind']=='opencity':
                doc=json.loads(body);hits=list(doc.get('searchHits',[]));page_number=1
                while doc.get('nextPageQuery'):
                    query=doc['nextPageQuery'];page_number+=1
                    if page_number>10 or not re.fullmatch(r'classes \[event\] limit 100 offset \d+',query):raise ValueError('Paginazione comunale inattesa')
                    more=(args.fixtures/f'comune-{page_number}.json').read_bytes() if args.fixtures else fetch(source['url'].split('?')[0]+'?q='+quote(query,safe=''),source)
                    doc=json.loads(more);hits+=doc.get('searchHits',[])
                raw,issues=opencity_events({'searchHits':hits},source,today)
                if len(hits)<json.loads(body).get('totalCount',0):raise ValueError('Calendario comunale parziale')
            elif source['kind']=='parco_pdf':
                def document(url):return (args.fixtures/source['documentFixture']).read_bytes() if args.fixtures else fetch(url,source)
                raw,issues,document_hash=parco_events(body,source,today,document);check['documentSha256']=document_hash
            else:
                page=parse_page(body);raw,issues=jsonld_events(body,source,today)
                if not args.fixtures:
                    links=sorted({urljoin(source['url'],x) for x in page.links if urlsplit(urljoin(source['url'],x)).path.startswith(source['detailPath'])})[:30]
                    for link in links:
                        try:
                            extra,extra_issues=jsonld_events(fetch(link,source),{**source,'page':link},today);raw+=extra;issues+=extra_issues
                        except Exception as error:issues.append('Scheda non leggibile: '+str(error)[:120])
                if not raw:issues.append('Il calendario comunale non espone eventi strutturati leggibili: nessun evento dedotto dal testo o dalla data di pubblicazione.')
            check['ok']=True;check['issues']=issues;check['eventCount']=len(raw)
            if not issues and source['kind']=='ical':healthy.add(source['id'])
            if not issues and source['kind'] in ['parco_pdf','opencity']:healthy.add(source['id'])
            for row in raw:
                event=entity(row,source,today)
                event['automation']['lastSeenAt']=now
                matching=[e for e in old.get('events',[]) if e.get('automation',{}).get('sourceId')==source['id'] and e.get('automation',{}).get('identity')==row['identity']]
                if not matching:matching=[e for e in seed if e['startDate']==row['startDate'] and key(e['name']['it'])==key(row['name'])]
                if len(matching)==1:
                    event['id']=matching[0]['id'];event['routes']=matching[0]['routes'];event['gallery']=matching[0].get('gallery',[])
                blocked=[e for e in seed if e.get('status','').startswith(('blocked','pending')) and key(e['name']['it'])==key(row['name'])]
                if blocked:
                    event['publication']='draft';event['status']='pending_automation_review';event['automation']['issue']='Conflitto editoriale già segnalato nel catalogo'
                if event['publication']=='draft' and event['endDate']>=today.isoformat():
                    check['issues'].append(event['name']['it'][:90]+': '+event['automation']['issue'])
                found.append(event)
        except Exception as error:
            check['issues']=[type(error).__name__+': '+str(error)[:240]]
        check['issues']=sorted(check['issues'])
        checks.append(check)
        all_sources.append({'id':'AUTO-'+source['id'],'owner':source['owner'],'title':'Calendario ufficiale: '+source['owner'],'url':source['page'],'checked':today.isoformat(),'note':'Importazione automatica di dati espliciti. Nessuna fotografia o descrizione editoriale copiata.'})
    # Same facts from two calendars are represented once. Conflicting dates remain separate and flagged.
    unique={}
    for e in found:
        identity=(key(e['name']['it']),e['startDate'],e['endDate'],e['startTime'])
        if identity not in unique:unique[identity]=e
    events=reconcile(old.get('events',[]),list(unique.values()),healthy,today)
    for event in events:
        if event['automation']['uid'] in cancelled_uids.get(event['automation']['sourceId'],set()):
            event['eventStatus']='cancelled';event['publication']='archive';event['status']='reviewed'
    feed={'schemaVersion':1,'generatedAt':now,'events':events,'sources':all_sources,'sourceChecks':checks,'maxStaleHours':config['maxStaleHours']}
    changed=public_fingerprint(old)!=public_fingerprint(feed)
    args.output.mkdir(parents=True,exist_ok=True);write_json(args.output/'proposal.json',feed)
    previous_state=read_json(ROOT/'state.json',{})
    state={'checkedAt':now,'sources':checks,'lastPublishedFingerprint':public_fingerprint(feed)}
    previous_alert=alert_fingerprint(previous_state['sources']) if 'sources' in previous_state else previous_state.get('alertFingerprint')
    state['alertFingerprint']=alert_fingerprint(checks)
    source_alert_changed=state['alertFingerprint']!=previous_alert
    write_json(args.output/'state.json',state)
    if args.apply:
        write_json(ROOT/'events-feed.json',feed);write_json(ROOT/'state.json',state)
        write_json(ROOT/'public/assets/data/events-feed.json',feed)
    report=['# MTV City · Aggiornamento automatico eventi',f'Controllo: {now}',f'Eventi gestiti: {len(events)}. Nuove occorrenze lette: {len(found)}.',f'Variazioni: {"sì" if changed else "no"}.','']
    for c in checks:
        report.append(f'- {c["owner"]}: {"letta" if c["ok"] else "errore"}; eventi {c.get("eventCount",0)}.')
        report.extend('  - '+plain(x) for x in c['issues'])
    report+=['','Le ricorrenze sono espanse solo nei limiti della fonte. Titoli nella lingua originale; descrizioni IT/EN redatte da modello fisso, senza ricopiare i testi. Nessuna foto esterna importata.']
    (args.output/'report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    result={'changed':changed,'attention':source_alert_changed,'has_issues':any(c['issues'] for c in checks),'events':len(events),'published':sum(e['publication']=='published' for e in events),'sourcesOk':sum(c['ok'] for c in checks),'report':str(args.output/'report.md'),'applied':args.apply}
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as f:
            for k in ['changed','attention','has_issues']:f.write(f'{k}={str(result[k]).lower()}\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['sourcesOk'] else 2

if __name__=='__main__':sys.exit(main())
