# Generated by Django 3.0.4 on 2020-05-26 22:12

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('stocktradingapp', '0003_auto_20200526_1909'),
    ]

    operations = [
        migrations.AlterField(
            model_name='kiteconnectapp',
            name='api_key',
            field=models.CharField(max_length=100, unique=True),
        ),
        migrations.AlterField(
            model_name='kiteconnectapp',
            name='api_secret',
            field=models.CharField(max_length=100, unique=True),
        ),
        migrations.AlterField(
            model_name='zerodhaaccount',
            name='broker',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='zerodhaaccount',
            name='user_id',
            field=models.CharField(max_length=100, unique=True),
        ),
        migrations.AlterField(
            model_name='zerodhaaccount',
            name='user_type',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.CreateModel(
            name='LiveMonitor',
            fields=[
                ('user_id', models.CharField(max_length=100, primary_key=True, serialize=False)),
                ('initial_value', models.FloatField()),
                ('current_value', models.FloatField()),
                ('stoploss', models.FloatField()),
                ('value_at_risk', models.FloatField()),
                ('profit_percent', models.FloatField()),
                ('do_trading', models.BooleanField(default=True)),
                ('hstock_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_live_monitor', to=settings.AUTH_USER_MODEL, unique=True)),
            ],
        ),
    ]
