from PIL import Image
from urllib import urlencode
from collections import namedtuple
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from time import sleep
import random
import datetime
import pytz
import time
import pytz
import jinja2
import os
import requests
import epd7in5b_V2 as eink


import sys
reload(sys)
sys.setdefaultencoding('utf-8')
import json


CalendarEvent = namedtuple('CalendarEvent', 'summary hour')
CalendarEvents = namedtuple('CalendarEvents', 'date pretty_date events')
WeatherItem = namedtuple('WeatherItem', 'temperature weather_id precipitation hour')
Weather = namedtuple('Weather', 'today hourly rain_alert')
TubeStatus = namedtuple('TubeStatus', 'line status css_class')

LondonTimezone = 'Europe/London'
Width = 800
Height = 480
Format_YMD = '%Y-%m-%d'
HtmlOutputFile = 'dash.html'
ConfigFile = 'config.json'
RefreshTokenKey = 'refresh_token'
BearerKey = 'bearer'
BearerExpirationKey = 'bearer_experiration'
ApiKey = 'google_api_key'
ClientId = 'google_client_id'
ClientSecret = 'google_client_secret'
CalendarIds = 'calendar_ids'
WeatherAppId = 'weather_app_id'

class DisplayHelper:

    def __init__(self, width, height):
        # Initialise the display
        self.screenwidth = width
        self.screenheight = height
        self.epd = eink.EPD()
        self.epd.init()

    def clear(self):
        self.epd.Clear()

    def update(self, blackimg, redimg):
        # Updates the display with the grayscale and red images
        # start displaying on eink display
        self.epd.display(self.epd.getbuffer(blackimg), self.epd.getbuffer(redimg))
        print('E-Ink display update complete.')

    def calibrate(self, cycles=1):
        # Calibrates the display to prevent ghosting
        white = Image.new('1', (self.screenwidth, self.screenheight), 'white')
        black = Image.new('1', (self.screenwidth, self.screenheight), 'black')
        for _ in range(cycles):
            self.epd.display(self.epd.getbuffer(black), self.epd.getbuffer(white))
            sleep(1)
            self.epd.display(self.epd.getbuffer(white), self.epd.getbuffer(black))
            sleep(1)
            self.epd.display(self.epd.getbuffer(white), self.epd.getbuffer(white))
            sleep(1)
        print('E-Ink display calibration complete.')

    def sleep(self):
        # send E-Ink display to deep sleep
        self.epd.sleep()
        print('E-Ink display entered deep sleep.')

def pretty_date(Format_YMD, date):
    today_YMD = datetime.date.today().strftime(Format_YMD)
    formatted_date = datetime.datetime.strptime(date, Format_YMD).strftime('%a, %b %d')
    if today_YMD == date:
        return "Today"
    else:
        return formatted_date

# Returns CalendarEvent grouped by dates.
def fetch_calendar_events_for_calendar_id(calendar_id, api_key, events):
    today_YMD = datetime.date.today().strftime(Format_YMD)
    # It is important to use a timeMax. One event with many repeated instances
    # might contribute many results. We might not get all the events within the
    # max results returned by the API if we do not use a timeMax.
    next_month = datetime.date.today() + datetime.timedelta(days=30)
    next_month_YMD = next_month.strftime(Format_YMD)
    params = {
        'key': api_key,
        'timeMin': today_YMD + 'T00:00:00Z',
        'timeMax': next_month_YMD + 'T00:00:00Z',
        'singleEvents': True,
        'timeZone': LondonTimezone
    }

    headers = {
        'Authorization': 'Bearer ' + config[BearerKey]
    }
    url = 'https://www.googleapis.com/calendar/v3/calendars/' + calendar_id + '/events'
    print 'calendar request ', url, params
    response = requests.get(url=url, headers=headers, params=params)
    response.raise_for_status()
    for item in response.json()['items']:
        if 'kind' not in item or 'summary' not in item:
            continue

        if item['kind'] != 'calendar#event':
            continue

        # Get our date, formatted as YYYY-MM-DD
        event_YMD = ''
        event_hour = None
        start = item['start']
        if 'dateTime' in start:
            parsed_date = datetime.datetime.strptime(item['start']['dateTime'][:19], '%Y-%m-%dT%H:%M:%S')
            event_YMD = parsed_date.strftime(Format_YMD)
            event_hour = parsed_date.strftime('%H:%M')
        else:
            event_YMD = datetime.datetime.strptime(item['start']['date'], '%Y-%m-%d').strftime(Format_YMD)
            event_hour = '00:00'

        event = CalendarEvent(summary=item['summary'].encode('utf-8'), hour=event_hour)
        # print calendar_id, event_YMD, event
        try:
            events[event_YMD].append(event)
        except:
            events[event_YMD] = [event]

# Fetches, dedup and group event calendars by date.
def fetch_calendar_events(calendar_ids, api_key):
    events = {}
    for i in calendar_ids:
        fetch_calendar_events_for_calendar_id(i, api_key, events)

    today_YMD = datetime.date.today().strftime(Format_YMD)
    calendar_events = [CalendarEvents(
        date=key, 
        pretty_date=pretty_date(Format_YMD, key),
        events=value) for key, value in events.items() if key >= today_YMD]
    calendar_events.sort(key=lambda i: i.date)
    sorted_events = []
    for e in calendar_events:
        # Remove duplicate events. that can happen since we pull events from multiple calendars.
        unique_values = list(set(e.events))
        #unique_values.sort(key=lambda i: i.hour)
        sorted_events.append(e._replace(events=unique_values))
    if len(sorted_events) == 0:
        return []
    if sorted_events[0].pretty_date != "Today":
        sorted_events.insert(0, CalendarEvents(
            date=today_YMD, pretty_date="Today", events=[]))
    return sorted_events

def create_weather(openweather_response):
    return WeatherItem(
            temperature=round(openweather_response['temp']),
            precipitation=None,
            hour=None,
            weather_id=openweather_response['weather'][0]['id'])

def create_weather_hourly(openweather_response):
    return WeatherItem(
            temperature=round(openweather_response['temp']),
            precipitation=round(openweather_response['pop'] * 100),
            hour=datetime.datetime.fromtimestamp(openweather_response['dt']).strftime('%H:%M'),
            weather_id=openweather_response['weather'][0]['id'])

# Fetches weather updates.
def fetch_weather(weather_app_id):
    payload = {
    'APPID': weather_app_id,
    # Islington
    # https://www.findlatitudeandlongitude.com/?lat=51.564175&lon=4.662431&zoom=&map_type=ROADMAP
    'lat': '51.5465',
    'lon': '-0.10304',
    'units': 'metric',
    }

    weather_response = requests.get('https://api.openweathermap.org/data/2.5/onecall', params = payload).json()
    # Weather id will be converted using https://github.com/websygen/owfont/
    current = weather_response['current']
    hourly = [create_weather_hourly(i) for i in weather_response['hourly']]
    # Set rain alert if there is a chance of more than 10% of rain in the next 12h.
    rain_alert = any([i.precipitation > 10 for i in hourly[:12]])
    return Weather(today=create_weather(current), hourly=hourly, rain_alert=rain_alert)

def create_tube_status(line, status):
    if status == 'Good Service':
       css_style = ''
    else:
       css_style = 'tube_disruption'
    return TubeStatus(line=line, status=status, css_class=css_style);

def fetch_tube_status():
    tube_response = requests.get('https://api.tfl.gov.uk/line/mode/tube/status').json()
    statuses = []
    for tube_line in tube_response:
        if tube_line['id'] == 'victoria' or tube_line['id'] == 'piccadilly':
            for line_status in tube_line['lineStatuses']:
                statuses.append(create_tube_status(tube_line['name'], line_status['statusSeverityDescription']))
    return statuses

def render_to_html(tube, events, weather):
    script_path = os.path.dirname(os.path.abspath(__file__))
    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(script_path))
    return environment.get_template('dash.html.j2').render({
        'events': events,
        'weather': weather,
        'tube': tube,
        'width': Width,
        'height': Height})

def write_to_file(output_file, html):
    f = open(output_file, "w")
    f.write(html.encode('utf-8'))
    f.close()

def set_viewport_size(driver, imageWidth, imageHeight):
    # Extract the current window size from the driver
    current_window_size = driver.get_window_size()

    # Extract the client window size from the html tag
    html = driver.find_element_by_tag_name('html')
    inner_width = int(html.get_attribute("clientWidth"))
    inner_height = int(html.get_attribute("clientHeight"))

    # "Internal width you want to set+Set "outer frame width" to window size
    target_width = imageWidth + (current_window_size["width"] - inner_width)
    target_height = imageHeight + (current_window_size["height"] - inner_height)

    driver.set_window_rect(
        width=target_width,
        height=target_height)

def html_to_png(imageWidth, imageHeight, html_file):
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--hide-scrollbars");
    #opts.add_argument('--force-device-scale-factor=1')
    driver = webdriver.Chrome(options=opts)
    set_viewport_size(driver, imageWidth, imageHeight)
    html_address = 'file://' + os.path.dirname(os.path.abspath(__file__)) + '/' + html_file
    output_file = os.path.dirname(os.path.abspath(__file__)) + '/calendar.png'
    driver.get(html_address)
    sleep(1)
    driver.get_screenshot_as_file(output_file)
    driver.quit()

    print('Screenshot captured and saved to file.')

    redimg = Image.open(output_file)  # get image)
    rpixels = redimg.load()  # create the pixel map
    blackimg = Image.open(output_file)  # get image)
    bpixels = blackimg.load()  # create the pixel map

    for i in range(redimg.size[0]):  # loop through every pixel in the image
        for j in range(redimg.size[1]): # since both bitmaps are identical, cycle only once and not both bitmaps
            if rpixels[i, j][0] <= rpixels[i, j][1] and rpixels[i, j][0] <= rpixels[i, j][2]:  # if is not red
                rpixels[i, j] = (255, 255, 255)  # change it to white in the red image bitmap

            elif bpixels[i, j][0] > bpixels[i, j][1] and bpixels[i, j][0] > bpixels[i, j][2]:  # if is red
                bpixels[i, j] = (255, 255, 255)  # change to white in the black image bitmap

    print('Image colours processed. Extracted grayscale and red images.')
    return blackimg, redimg

def refresh_auth_token_if_needed(config):
  now = time.time()
  if (now < config[BearerExpirationKey]):
    print('Access token not expired')
    return

  data = {
    'client_id': config[ClientId],
    'client_secret': config[ClientSecret],
    'grant_type': 'refresh_token',
    'refresh_token': config[RefreshTokenKey]
  }
  response = requests.post(url='https://oauth2.googleapis.com/token', data=data)
  response.raise_for_status()
  config[BearerKey] = response.json()['access_token']
  config[BearerExpirationKey] = now + response.json()['expires_in']

  print 'Access token updated'

def write_config(config):
    with open(ConfigFile, "w") as jsonfile:
        jsonfile.write(json.dumps(config))

def fetch_data(calendar_ids, api_key, weather_app_id):
    tube = fetch_tube_status()
    print 'Tube status'
    for t in tube:
        print t
    print '\n\n'

    events = fetch_calendar_events(calendar_ids, api_key)
    print 'Calendar events'
    for event in events:
        print event
    print '\n\n'

    print 'Weather'
    weather = fetch_weather(weather_app_id)
    print weather

    return tube, events, weather

def update_display(calBlackImage, calRedImage):
    # Doc https://www.waveshare.com/wiki/7.5inch_e-Paper_HAT_(B)
    displayService = DisplayHelper(Width, Height)
    if random.randint(0, 20) == 0:
        displayService.clear()
    displayService.update(calBlackImage, calRedImage)
    displayService.sleep()

def open_config():
    config = {}
    with open(ConfigFile) as config_file:
        config = json.load(config_file)
    return config


dt = datetime.datetime.now(pytz.timezone('Europe/London'))
if dt.strftime('%H%M') == '0300':
    # Not sure if this is necessary to avoid ghosting.
    print 'Calibrating...'
    displayService = DisplayHelper(Width, Height)
    displayService.calibrate(cycles=10)
else:
    config = open_config()
    refresh_auth_token_if_needed(config)
    tube, events, weather = fetch_data(config[CalendarIds], config[ApiKey], config[WeatherAppId])
    html = render_to_html(tube, events, weather)
    write_to_file(HtmlOutputFile, html)
    write_config(config)
    calBlackImage, calRedImage = html_to_png(Width, Height, HtmlOutputFile)
    update_display(calBlackImage, calRedImage)
