# Generated by Django 3.0.4 on 2020-03-31 17:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stocktradingapp', '0002_zerodhaaccount_fund_available'),
    ]

    operations = [
        migrations.AddField(
            model_name='stock',
            name='co_trigger_percent_lower',
            field=models.FloatField(default=0.0),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='stock',
            name='co_trigger_percent_upper',
            field=models.FloatField(default=0.0),
            preserve_default=False,
        ),
    ]