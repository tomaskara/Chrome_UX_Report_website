import smtplib, ssl
import os
import json
import requests
import datetime
from statistics import mean
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from django.conf import settings
from speedcheck.models import Urls, CruxHistory, ProfileUrl

BASE_DIR = settings.BASE_DIR
# project_folder = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))


def get_all_urls_data():
    for url in Urls.objects.all():
        get_api_data(url.url)
    return


def get_api_data(url):
    api_url = f"https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={os.getenv('API_KEY')}"
    shortcuts = {"largest_contentful_paint": "lcp",
                 "first_input_delay": "fid",
                 "cumulative_layout_shift": "cls",
                 "first_contentful_paint": "fcp",
                 "experimental_time_to_first_byte": "ttfb",
                 "experimental_interaction_to_next_paint": "inp"}
    for device in ["PHONE", "DESKTOP"]:
        request_body = {

            "url": url,
            "formFactor": device,
            "metrics": [
                "largest_contentful_paint",
                "first_input_delay",
                "cumulative_layout_shift",
                "first_contentful_paint",
                "experimental_time_to_first_byte",
                "experimental_interaction_to_next_paint"
            ]
        }

        api_call = requests.post(api_url, json=request_body)
        if api_call.status_code == 404:
            continue
        json_response = api_call.json()
        date = datetime.date(json_response['record']['collectionPeriod']['lastDate']['year'],
                             json_response['record']['collectionPeriod']['lastDate']['month'],
                             json_response['record']['collectionPeriod']['lastDate']['day'])

        if device == "PHONE":
            # check if combination url and date exists in db, if not: create new record with values for mobile
            if not CruxHistory.objects.filter(url__url=url, date=date).exists():
                new_values = {"url": Urls.objects.get(url=url),
                              "date": date}
                for name, value in json_response['record']['metrics'].items():
                    new_values[f"{shortcuts[name]}m"] = float(value['percentiles']['p75'])
                CruxHistory.objects.create(**new_values)
                metrics_to_alert = email_trigger(url, new_values)
                if metrics_to_alert:
                    try:
                        email_launcher(url, metrics_to_alert)
                    except:
                        pass
        elif device == "DESKTOP":
            # check if combination url and date exists with empty desktop values, if yes: update desktop values
            if CruxHistory.objects.filter(url__url=url, date=date, clsd__isnull=True).exists():
                new_values = {}
                for name, value in json_response['record']['metrics'].items():
                    new_values[f"{shortcuts[name]}d"] = float(value['percentiles']['p75'])
                object_to_update = CruxHistory.objects.filter(url__url=url, date=date)
                object_to_update.update(**new_values)
    return


def email_trigger(url, new_values):
    query_set_for_url = CruxHistory.objects.filter(url__url=url).order_by('-date')
    query_list = [entry for entry in query_set_for_url]  # evaluate query set to cache results
    metrics_to_alert = {}
    trigger = False
    if len(query_set_for_url) >= 6:
        clsm = [q.clsm for q in query_set_for_url[1:6]]
        fidm = [q.fidm for q in query_set_for_url[1:6]]
        lcpm = [q.lcpm for q in query_set_for_url[1:6]]
        if new_values['clsm'] > mean(clsm):
            metrics_to_alert["clsm"] = [new_values['clsm'], mean(clsm)]
            trigger = True
        if new_values['fidm'] > mean(fidm):
            metrics_to_alert["fidm"] = [new_values['fidm'], mean(fidm)]
            trigger = True
        if new_values['lcpm'] > mean(lcpm):
            metrics_to_alert["lcpm"] = [new_values['lcpm'], mean(lcpm)]
            trigger = True
        if trigger:
            return metrics_to_alert

    else:
        return False


def email_launcher(url, metrics_to_alert):
    alerts = ProfileUrl.objects.filter(email_alert=True, url__url=url)
    for alert in alerts:
        user_email = alert.profile.user.email
        send_email(user_email, url, metrics_to_alert)


def send_email(user_email, url, metrics_to_alert):
    port = 465  # For SSL
    password = os.getenv('EMAIL_PASSWORD')
    sender_email = os.getenv('EMAIL')
    metrics = [x for x in metrics_to_alert.keys()]
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.seznam.cz", port, context=context) as server:
        server.login(sender_email, password)
        if True:
            message_root = MIMEMultipart("related")
            message_root["Subject"] = f"ALERT - rychlost pro {url} se zhor??ila v metrik??ch: {', '.join(metrics)}"
            message_root["From"] = sender_email
            message_root["To"] = user_email
            msg_body = MIMEMultipart("alternative")
            text = f"""\
          Rychlost metrik {' a '.join(metrics)} stoupla oproti pr??m??ru za posledn??ch 5 dn??.
          {os.linesep.join(f"Aktu??ln?? hodnota metriky {key} je {value[0]} a pr??m??r je {value[1]}" for key, value in metrics_to_alert.items())}"""

            html = f"""\
          <html>
            <body>
              <p>Rychlost metrik {' a '.join(metrics)} stoupla oproti pr??m??ru za posledn??ch 5 dn??.
                {os.linesep.join(f"Aktu??ln?? hodnota metriky {key} je {value[0]} a pr??m??r je {value[1]}" for key, value in metrics_to_alert.items())}          
              </p>
            </body>
          </html>
          """

            # Turn these into plain/html MIMEText objects
            part1 = MIMEText(text, "plain")
            part2 = MIMEText(html, "html")

            msg_body.attach(part1)
            msg_body.attach(part2)
            message_root.attach(msg_body)
            server.sendmail(
                sender_email, user_email, message_root.as_string()
            )
