import hashlib
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import render

# Create your views here.
from django.urls import reverse
from kiteconnect import KiteConnect

from stock_project import settings
from stocktradingapp import stocktrader
from stocktradingapp.models import ZerodhaAccount

logging.basicConfig(filename=settings.LOG_FILE_PATH, level=logging.DEBUG)

@login_required
def index(request):
    context = {
        'user_zerodha':request.user.user_zerodha.first()
    }
    return render(request, template_name='stocktradingapp/user_home.html', context=context)

@login_required
def authZerodha(request):
    user_kite_app = request.user.user_kite_app.first()
    kite = KiteConnect(user_kite_app.api_key)
    return HttpResponseRedirect(kite.login_url())

@login_required
def authRedirect(request):
    logging.debug('get query params - {}'.format(request.GET))
    request_token = request.GET['request_token']
    user_kite_app = request.user.user_kite_app.first()
    kite = KiteConnect(user_kite_app.api_key)
    zerodha_user_data = kite.generate_session(request_token=request_token, api_secret=user_kite_app.api_secret)
    user_zerodha = request.user.user_zerodha.first()
    if user_zerodha is None:
        user_zerodha = ZerodhaAccount(hstock_user=request.user)
    user_zerodha.access_token = zerodha_user_data['access_token']
    user_zerodha.refresh_token = zerodha_user_data['refresh_token']
    user_zerodha.public_token = zerodha_user_data['public_token']
    user_zerodha.api_key = zerodha_user_data['api_key']
    user_zerodha.user_id = zerodha_user_data['user_id']
    user_zerodha.user_name = zerodha_user_data['user_name']
    user_zerodha.user_shortname = zerodha_user_data['user_shortname']
    user_zerodha.email = zerodha_user_data['email']
    user_zerodha.user_type = zerodha_user_data['user_type']
    user_zerodha.broker = zerodha_user_data['broker']
    user_zerodha.exchanges = json.dumps(zerodha_user_data['exchanges'])
    user_zerodha.products = json.dumps(zerodha_user_data['products'])
    user_zerodha.order_types = json.dumps(zerodha_user_data['order_types'])
    user_zerodha.save()
    return HttpResponseRedirect(reverse(index))

def zerodhaPostback(request):
    try:
        logging.debug('\n\n\nreceived placed order details : \n\n\n{}'.format(request.body))
    except Exception as e:
        logging.debug('{}'.format(e))
    try:
        order_details = json.loads(request.body)
        logging.debug('\n\n\nreceived placed order details json format : \n\n\n{}'.format(order_details))
    except Exception as e:
        logging.debug('{}'.format(e))

    # if not verifyCheckSum(order_details):
    #     return
    return None

def verifyCheckSum(order_details):
    user_zerodha = ZerodhaAccount.objects.filter(user_id = order_details['user_id'])
    kite_app = user_zerodha.hstock_user.user_kite_app.first()
    if kite_app is None:
        return True
    api_secret = kite_app.api_secret
    stringToHash = order_details['order_id'] + order_details['order_timestamp'] + api_secret
    hashedString = hashlib.sha256(stringToHash.encode()).hexdigest()
    if hashedString == order_details['checksum']:
        return True
    return False
