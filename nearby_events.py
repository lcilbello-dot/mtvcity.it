"""Read explicit dates and venues from the neighbouring official calendars."""
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlsplit
import re

from lxml import html


MONTHS = {name: i for i, name in enumerate(
    'gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split(), 1
)}
MONTH = '|'.join(MONTHS)
DAY = rf'(\d{{1,2}})\s+({MONTH})\s+(20\d{{2}})'
RANGE = rf'dal\s+(\d{{1,2}})(?:\s+({MONTH})(?:\s+(20\d{{2}}))?)?\s+al\s+{DAY}'
CLOCK = r'([01]?\d|2[0-3])[:.]([0-5]\d)'
CLASS = 'contains(concat(" ",normalize-space(@class)," ")," {} ")'


def css_class(name):
    return CLASS.format(name)


def document(body):
    # All five endpoints declare UTF-8. BR marks separate facts in source calendars.
    return html.fromstring(body.decode('utf-8-sig'), parser=html.HTMLParser())


def text(node):
    if isinstance(node, str):
        return ' '.join(node.split())
    return ' '.join(' '.join(node.itertext()).split())


def first(node, xpath):
    found = node.xpath(xpath)
    return text(found[0]) if found else ''


def italian_date(day, month, year):
    return date(int(year), MONTHS[month.lower()], int(day))


def event_status(title):
    if re.search(r'\bannullat[oaie]\b', title, re.I):
        return 'cancelled'
    if re.search(r'\b(?:rinviat|rimandat)[oaie]\b', title, re.I):
        return 'postponed'
    return 'scheduled'


def row(title, url, start, end, venue, municipality, hour=None, category='cultura', uid=None):
    if not all((title, venue, municipality)) or end < start:
        raise ValueError('Titolo, date o luogo incompleti')
    uid = uid or url
    return {
        'identity': f'{uid}|{start.isoformat()}', 'uid': uid, 'name': title,
        'startDate': start.isoformat(), 'endDate': end.isoformat(), 'startTime': hour,
        'dayPart': hour or 'Orari sul programma ufficiale', 'place': venue,
        'officialUrl': url, 'eventStatus': event_status(title), 'calendar': True,
        'municipality': municipality, 'category': category,
    }


def municipal(body, source, get, url_for):
    calendar = document(body)
    links = calendar.xpath('//a[@data-element="event-link"]/@href')
    if not links and not calendar.xpath('//input[@id="data_eve_dal"]'):
        raise ValueError('Elenco eventi comunale assente')
    total = first(calendar, '//*[@id="totalePagine"]')
    issues = ['Il calendario comunale ha ulteriori pagine'] if total.isdecimal() and int(total) > 1 else []
    events = []
    for link in dict.fromkeys(links):
        try:
            url = url_for(link)
            if not re.fullmatch(r'/vivere_il_comune/eventi/evento_\d+\.html', urlsplit(url).path):
                raise ValueError('Indirizzo scheda evento inatteso')
            page = document(get(url))
            title = first(page, '//h1[@id="titolo_social"]')
            dates = page.xpath(f'//*[@id="time"]//*[{css_class("calendar-date")}]')
            if not title or not dates:
                raise ValueError('Titolo o calendario della scheda assente')
            for occurrence in dates:
                date_block = first(occurrence, f'.//*[{css_class("date-range")}]')
                values = re.findall(r'\b\d{2}-\d{2}-20\d{2}\b', date_block)
                if len(values) not in (1, 2):
                    raise ValueError('Date della scheda incomplete')
                start, end = [datetime.strptime(value, '%d-%m-%Y').date() for value in (values[0], values[-1])]
                place = first(occurrence, './/a[contains(@href,"/luoghi/")]//span')
                if not place:
                    place = first(page, '//*[@id="indirizzo"]//h3')
                time_block = first(occurrence, f'.//*[{css_class("calendar-date-day")}]')
                clock = re.search(CLOCK, time_block) if start == end else None
                hour = f'{int(clock[1]):02}:{clock[2]}' if clock else None
                events.append(row(title, url, start, end, place, source['municipality'], hour))
        except (ValueError, OSError) as error:
            issues.append(f'{urlsplit(link).path}: {error}')
    return events, issues


def greppi(body, source, _get, url_for):
    calendar = document(body)
    cards = calendar.xpath(f'//div[{css_class("event")} and starts-with(@id,"event-")]')
    if not cards:
        raise ValueError('Schede di Villa Greppi assenti')
    events, issues = [], []
    for card in cards:
        title_box = card.xpath(f'./div/div[{css_class("title")}]')
        if not title_box:
            continue
        title_box = title_box[0]
        facts = first(title_box, './p[last()]')
        municipality = re.search(r'([^\d]+?)\s*\(LC\)\s*$', facts, re.I)
        if not municipality:
            continue
        city_text = re.sub(r'^.*(?:ore\s+)?\b\d{1,2}[.:]\d{2}\s*', '', municipality[1], flags=re.I).strip()
        city = next((c for c in source['municipalities'] if city_text.casefold().endswith(c.casefold())), None)
        if not city:
            continue
        try:
            title = first(title_box, './h2')
            span = re.search(RANGE, facts, re.I)
            clock = re.search(r'\bore\s+' + CLOCK, facts, re.I)
            if span:
                end = italian_date(span[4], span[5], span[6])
                start = italian_date(span[1], span[2] or span[5], span[3] or span[6])
                hour = None
            else:
                dates = re.findall(DAY, facts, re.I)
                if len(dates) != 1:
                    raise ValueError('Data pubblicata non univoca')
                start = end = italian_date(*dates[0])
                hour = f'{int(clock[1]):02}:{clock[2]}' if clock else None
            url = url_for(first(card, './/a[@data-link]/@data-link'))
            if not urlsplit(url).path.startswith('/evento/'):
                raise ValueError('Collegamento alla scheda assente')
            places = card.xpath(f'.//div[{css_class("text")}]/p[br]')
            city_line = re.compile(r'(?:^|\s)' + re.escape(city) + r'(?:\s*\(LC\))?$', re.I)
            venue = next((text(place) for place in reversed(places) if city_line.search(text(place))), '')
            events.append(row(title, url, start, end, venue, city, hour))
        except ValueError as error:
            issues.append(f'{card.get("id")}: {error}')
    return events, issues


def market(body, source, _get, url_for):
    page = document(body)
    tables = page.xpath(f'//table[{css_class("tabella-date")}]')
    if not tables:
        raise ValueError('Tabella ufficiale delle date assente')
    events, issues = [], []
    for table in tables:
        heading = first(table, './preceding-sibling::p[contains(., "Date")][1]')
        year = re.fullmatch(r'Date (20\d{2})', heading)
        if not year:
            issues.append('Anno del mercatino assente')
            continue
        for value in (text(cell) for cell in table.xpath('.//td')):
            if not value:
                continue
            match = re.fullmatch(rf'(\d{{1,2}})\s+({MONTH})', value, re.I)
            if not match:
                issues.append('Data del mercatino non riconosciuta')
                continue
            try:
                day = italian_date(match[1], match[2], year[1])
                events.append(row('Mercatino dell’Antico', url_for(source['page']), day, day,
                                  'Piazza Garibaldi e vie del centro', source['municipality'],
                                  category='mercatini'))
            except ValueError as error:
                issues.append(str(error))
    return events, issues


def theatre(body, source, get, url_for):
    page = document(body)
    if '/eventi-' not in urlsplit(source['url']).path:
        years = set()
        for link in page.xpath('//a/@href'):
            match = re.fullmatch(r'/?(eventi-(20\d{2})-20\d{2})/[^/]+\.htm', link)
            if match:
                years.add((int(match[2]), '/' + match[1] + '/stagione-teatrale.htm'))
        if not years:
            raise ValueError('Collegamento alla stagione teatrale assente')
        season = url_for(max(years)[1])
        page = document(get(season))
    else:
        season = source['url']
    events, issues = [], []
    listed = page.xpath(f'//div[{css_class("gray")}]//p[a[@href]]')
    for paragraph in listed:
        link = paragraph.xpath('./a[@href]')[0]
        title = text(link)
        date_text = text(paragraph).split(title, 1)[0] if title else ''
        dates = re.findall(DAY, date_text, re.I)
        if not dates:
            continue
        try:
            url = url_for(urljoin(season, link.get('href')))
            if not re.fullmatch(r'/eventi-20\d{2}-20\d{2}/[^/]+\.htm', urlsplit(url).path):
                raise ValueError('Indirizzo scheda spettacolo inatteso')
            detail = document(get(url))
            facts = detail.xpath(f'//*[@id="corpo2019"]//div[{css_class("style5")}]')
            times = {}
            for box in facts:
                for day, month, year, hour, minute in re.findall(DAY + r'\s*[-–]?\s*ore\s+' + CLOCK, text(box), re.I):
                    times[italian_date(day, month, year)] = f'{int(hour):02}:{minute}'
            for value in dates:
                day = italian_date(*value)
                events.append(row(title, url, day, day, 'Auditorium di Casatenovo', source['municipality'],
                                  times.get(day), 'teatro'))
        except (ValueError, OSError) as error:
            issues.append(f'{title}: {error}')
    if not events and not issues:
        raise ValueError('Date della stagione teatrale assenti')
    return events, issues


PARSERS = {'municipal_html': municipal, 'greppi_html': greppi,
           'market_html': market, 'theatre_html': theatre}


def collect(body, source, today, horizon, get, check_url):
    def url_for(value):
        return check_url(urljoin(source['url'], value), source['hosts'])

    rows, issues = PARSERS[source['kind']](body, source, get, url_for)
    earliest = (today - timedelta(days=14)).isoformat()
    latest = (today + timedelta(days=horizon)).isoformat()
    return [event for event in rows if event['endDate'] >= earliest and event['startDate'] <= latest], issues
