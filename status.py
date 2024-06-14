#!/usr/bin/python
# -*- coding:utf-8 -*-
import sys
import os
picdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'pic')
libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

import logging
from waveshare_epd import epd2in13_V3
import time
from PIL import Image,ImageDraw,ImageFont
# import traceback
from influxdb import InfluxDBClient
import latlon
import math
import click

from sensors.INA219 import INA219

logging.basicConfig(level=logging.INFO)

class UPS():
    def __init__(self, i2c_bus=1, addr=0x43):
        self.sensor = INA219(i2c_bus=i2c_bus,addr=addr) 
        
    def V(self):
        bus_voltage = self.sensor.getBusVoltage_V()
        logging.debug("Battery Load Voltage: {:6.3f} V".format(bus_voltage))
        return bus_voltage
    
    def mA(self):
        current = -1 * self.sensor.getCurrent_mA()
        logging.debug("Battery Current: {:6.3f} mA".format(current))
        return current
    
    def A(self):
        return self.mA()/1000

    def W(self):
        power = self.sensor.getPower_W()
        logging.debug("Battery Power: {:6.3f} W".format(power))
        return power
    
    def percent(self):
        min_voltage = 3.0 
        max_voltage = 4.2
        ratio = (self.V() - min_voltage)/(max_voltage-min_voltage)
        ratio = 0.0 if ratio < 0.0 else ratio
        ratio = 1.0 if ratio > 1.0 else ratio
        p = int(ratio * 100)
        logging.debug("Battery Percent charge: {:3.1f}%".format(p))
        return p

    def is_discharging(self):
        return self.mA() < 50 and self.V() < 4.0
    
class TimeSeries():
    def __init__(self, host='localhost', port = 8086, database = 'boatdata'):
        self.dbname = database
        self.db = InfluxDBClient(host=host, port=port, database=database)
        self.ensure_continuous_queries_exist()
        
    def ensure_continuous_queries_exist(self):    
        if not self.query_exists("environment_outside_pressure_mean_1h"):
            self. create_continuous_query_1h_mean("environment.outside.pressure")
        if not self.query_exists("environment_outside_temperature_mean_1h"):
            self. create_continuous_query_1h_mean("environment.outside.temperature")
        if not self.query_exists("environment_outside_humidity_mean_1h"):
            self. create_continuous_query_1h_mean("environment.outside.humidity")
        if not self.query_exists("navigation_position_last_1h"):
            self. create_continuous_query_1h_last("navigation.position",value="*")
        if not self.query_exists("navigation_position_first_1h"):
            self. create_continuous_query_1h_first("navigation.position", value="*")
        
    def query_exists(self, query_name):
        queries = self.db.get_list_continuous_queries()
        queries = [ a[self.dbname] for a in queries if a.get(self.dbname,None)]
        queries = queries[0] if queries else []
        logging.debug('boatdata queries: {}'.format(queries))
        my_query =[ a for a in queries if a.get('name',None) == query_name]
        logging.debug("my query: {}".format(my_query))
        return bool(my_query)

    def create_continuous_query_1h_mean(self, measurement):
        self.create_continuous_query(measurement, period="1h", resample_every="10m", function='mean')

    def create_continuous_query_1h_last(self, measurement, value='"value"'):
        self.create_continuous_query(measurement, period="1h", resample_every="10m", function='last', value=value)

    def create_continuous_query_1h_first(self, measurement, value='"value"'):
        self.create_continuous_query(measurement, period="1h", resample_every="10m", function='first', value=value)

    def create_continuous_query(self, measurement, function, period, resample_every, value='"value"'):
        new_name = measurement.split('.')
        new_name[-1] = "{}_{}_{}".format(new_name[-1], function, period) 
        new_name = ".".join(new_name)
        cq_name = new_name.replace('.','_')
        select_clause = """
            SELECT {function}({value}) 
            INTO "{new_measurement}" 
            FROM "{measurment}"
            GROUP BY time({period}) 
            """.format(
                function=function,
                value=value,
                new_measurement=new_name,
                measurment=measurement,
                period=period,
            )
        logging.info(select_clause)
        resample = "EVERY {}".format(resample_every)
        self.db.create_continuous_query(cq_name, select_clause, self.dbname, resample)

    def current_position(self):
        pos=self.db.query('select mean(lat) as lat,mean(lon) as lon from "navigation.position" where time >= now()-11s')
        position=[a for a in pos.get_points()]
        position=position[0] if position else None
        if position:
            ll=latlon.LatLon(position['lat'], position['lon'])
            return ll
        else:
            return None
    
    def current_cog_true(self):
        try:
            cog = self.db.query('select mean(value) from "navigation.courseOverGroundTrue" where time >= now()-10s')
            cog = "{cog:003.0f}".format(cog=[a for a in cog.get_points()][0]['mean']*(180/math.pi))
        except:
            cog = "---"
        return cog

    def current_cog_magnetic(self):
        try:
            cog = self.db.query('select mean(value) from "navigation.courseOverGroundMagnetic" where time >= now()-10s')
            cog = "{cog:003.0f}".format(cog=[a for a in cog.get_points()][0]['mean']*(180/math.pi))
        except:
            cog = "---"
        return cog

    def current_speed_over_ground(self):
        try:
            sog = self.db.query('select mean(value) from "navigation.speedOverGround" where time >= now()-10s')
            sog = [a for a in sog.get_points()][0]['mean']*1.943844
            sog = "{sog:02.1f}".format(sog=sog) if sog >=1 else "---"
        except:
            sog= "0.0"
        return sog

    def average_pressure_1h(self):
        value = self.db.query('select mean from "environment.outside.pressure_mean_1h" order by time desc limit 1')
        value = [a for a in value.get_points()]
        value = value[0]['mean'] if value else None
        #     # pressure in hPa = Pa/100
        
        return value/100 if value else 0

    def average_temperature_1h(self):
        value = self.db.query('select mean from "environment.outside.temperature_mean_1h" order by time desc limit 1')
        value = [a for a in value.get_points()]
        value = value[0]['mean'] if value else 0.0
        #     # temperature in C = K - 273.15
        return value-273.15 

    def average_humidity_1h(self):
        value = self.db.query('select mean from "environment.outside.humidity_mean_1h" order by time desc limit 1')
        value = [a for a in value.get_points()]
        value = value[0]['mean'] if value else -0.99
        #     # humidity in % = ratio * 100
        return value * 100
    
    def last_position_1h(self):
        value = self.db.query('select * from "navigation.position_last_1h" order by time desc limit 1')
        logging.info(value)
        try:
            position = [a for a in value.get_points()][0]
            last=latlon.LatLon(position['last_lat'], position['last_lon'])
            logging.info('last full hour position: {}'.format(last))
            return last
        except:
            return None

    def first_position_1h(self):
        value = self.db.query('select * from "navigation.position_first_1h" order by time desc limit 1')
        logging.info(value)
        try:
            position = [a for a in value.get_points()][0]
            first=latlon.LatLon(position['first_lat'], position['first_lon'])
            logging.info('previous full hour position: {}'.format(first))
            return first
        except:
            return None




class EInk():
    def __init__(self):
        self.epd = epd2in13_V3.EPD()
        self.epd.init()
        # epd.Clear(0xFF)
        
        #CAVEAT height is horizontal, width is vertical
        self.image = Image.new('1', (self.epd.height, self.epd.width), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.epd.displayPartBaseImage(self.epd.getbuffer(self.image))

        self.db = TimeSeries()
        self.ups = UPS()

        self.line_top_left = {
            "position": 0,
            "cogsog_1h":20,
            "env_1h": 40,
            "cogsog": 60,
            "desc": 90,
            "battery": 106,
        }
        self.font_size = {
            'position': 20,
            'cogsog_1h': 20,
            'env_1h': 20,
            'cogsog': 32,
            'desc': 16,
            'battery': 16,
        }
        self.font_monospace = {
            'position': True,
            'cogsog_1h': True,
            'env_1h': True,
            'cogsog': True,
            'desc': True,
            'battery': True,
        }


    def font(self, element_name):
        return ImageFont.truetype(
            os.path.join(picdir, 'Font.ttc'), 
            size=self.font_size[element_name], 
            index=1 if self.font_monospace[element_name] else 0)

    def clear_line(self, start, height, invert=False):
        self.draw.rectangle(((0, start), (self.epd.height, start+height)), fill = 0 if invert else 255)

    def battery_line_text(self):
        return "bat: {percent:2.0f}% {V:4.1f}V {A:4.1f}A {W:4.1f}W".format(
            percent = self.ups.percent(),
            V = self.ups.V(),
            A = self.ups.A(),
            W = self.ups.W()
        )
    
    def draw_battery_line(self):
        self.clear_line(
            self.line_top_left['battery'], 
            self.font_size['battery'], 
            invert = self.ups.is_discharging() 
        )
        self.draw.text( 
            (0, self.line_top_left['battery']),
            self.battery_line_text(), 
            font = self.font('battery'), 
            fill = 255 if self.ups.is_discharging() else 0
        )

    def current_position_text(self):
        ll = self.db.current_position()
        if ll:
            lon="{deg:03d}°{min:04.1f}'{hemi}".format(deg=int(ll.lon.degree), min=ll.lon.decimal_minute, hemi=ll.lon.get_hemisphere())
            lat="{deg:02d}°{min:04.1f}'{hemi}".format(deg=int(ll.lat.degree), min=ll.lat.decimal_minute, hemi=ll.lat.get_hemisphere())
        else:
            lon="--- --.-'X"
            lat="-- --.-'X"

        text = "{}  {}".format(lat,lon)
        logging.debug("Current Position: {}".format(text))
        return text
    
    def draw_current_position_line(self):
        self.clear_line( self.line_top_left['position'], self.font_size['position'], invert=False)
        self.draw.text(
            (0, self.line_top_left['position']),
            self.current_position_text(),
            font = self.font('position'),
            fill =  0
        )
    
    def cog_sog_text(self):
        return "{cog}\u00B0{cogm}\u00B0{sog}\u33CF".format(
            cog=self.db.current_cog_true(), 
            cogm=self.db.current_cog_magnetic(), 
            sog=self.db.current_speed_over_ground()
            )
    
    def draw_current_sog_cog_line(self):
        self.clear_line( self.line_top_left['cogsog'], self.font_size['cogsog'], invert=False)
        self.draw.text(
            (0, self.line_top_left['cogsog']),
            self.cog_sog_text(),
            font = self.font('cogsog'),
            fill = 0
        )
    
    def average_environment_data_over_1h_text(self):
        pressure_1h=self.db.average_pressure_1h()
        temperature_1h=self.db.average_temperature_1h()
        humidity_1h=self.db.average_humidity_1h()   
        text = "1h|{hPa:4.0f}hPa{C:3.0f}°C{hum:3.0f}%".format(
                hPa=pressure_1h,
                C=temperature_1h,
                hum=humidity_1h
                )
        return text


    def draw_average_environment_data_over_1h_line(self):
        self.clear_line( self.line_top_left['env_1h'], self.font_size['env_1h'], invert=False)
        self.draw.text(
            (0, self.line_top_left['env_1h']),
            self.average_environment_data_over_1h_text(),
            font = self.font('env_1h'),
            fill = 0
        )

    def cog_sog_over_1h_text(self):
        pos_1h_last = self.db.last_position_1h()
        pos_1h_first = self.db.first_position_1h()
        if not pos_1h_last or not pos_1h_first:
            sog_1h = "---"
            cog_1h = "---"
            return "1h|cog: {cog}, {sog}Nm".format(sog=sog_1h,cog=cog_1h)
        else:
            sog_1h = pos_1h_first.distance_sphere(pos_1h_last) * 0.54
            if sog_1h == 0.0:
                cog_1h = "---"
            elif sog_1h <=1:
                cog_1h = "KPL"
            else:
                cog_1h = "{:03d}°".format((round(pos_1h_first.heading_initial(pos_1h_last))+360)%360)
            return "1h| cog: {cog}, {sog:04.1f}Nm".format(sog=sog_1h,cog=cog_1h)
    
    def draw_cog_sog_over_1h_line(self):
        self.clear_line( self.line_top_left['cogsog_1h'], self.font_size['cogsog_1h'], invert=False)
        self.draw.text(
            (0, self.line_top_left['cogsog_1h']),
            self.cog_sog_over_1h_text(),
            font = self.font('cogsog_1h'),
            fill = 0
        )
    
    def draw_desc_line(self):
        self.clear_line( self.line_top_left['desc'], self.font_size['desc'], invert=False)
        self.draw.text(
            (0, self.line_top_left['desc']),
            "  true  magnetic  speed",
            font = self.font('desc'),
            fill = 0
        )
    

    def main_loop(self):
        logging.info("entering display main loop")
        try:
            num = 0 
            while (True):
                num = num % 60
                self.draw_current_position_line()
                self.draw_cog_sog_over_1h_line()
                self.draw_average_environment_data_over_1h_line()
                self.draw_current_sog_cog_line()
                self.draw_desc_line()
                self.draw_battery_line()

                if (num == 0):
                    self.epd.display(self.epd.getbuffer(self.image))    
                else:
                    self.epd.displayPartial(self.epd.getbuffer(self.image))
                
                num = num + 1
                time.sleep(1)

        finally:
            self.epd.init()
            self.epd.Clear(0xFF)
            self.epd.sleep()

if __name__ == "__main__":
    screen = EInk()
    screen.main_loop()
