# -*- coding: utf-8 -*-

import logging
#import rucio
#from rucio.client.client import Client

#from admix.runDB import xenon_runDB as XenonRunDatabase

from admix.tasks import helper
from admix.interfaces.database import DataBase

class tester():
    
    def __init__(self):
        pass
        #self.rucio_client = Client()
        #self.xrd = XenonRunDatabase.XenonRunDatabase()
        
    def init(self):
        print("overwrite init")
        db = DataBase()
        
    def run(self,*args, **kwargs):
        self.init()
        
        print("run tester")
        print(args)
        print(kwargs)
        
    def __del__(self):
        print( 'tester stop')
        