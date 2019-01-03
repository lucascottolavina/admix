# -*- coding: utf-8 -*-

import logging
import datetime
import time

#import rucio
#from rucio.client.client import Client

#from admix.runDB import xenon_runDB as XenonRunDatabase

import os
import json
from admix.tasks import helper
from admix.interfaces.database import DataBase, ConnectMongoDB
from admix.interfaces.rucioapi import ConfigRucioDataFormat, RucioAPI, RucioCLI, TransferRucio
from admix.interfaces.templater import Templater
from admix.interfaces.destination import Destination
from admix.interfaces.keyword import Keyword

class upload_with_mongodb():

    def __init__(self):
        print('Upload with mongoDB starts')

    def init(self):
        self.db = ConnectMongoDB()
        self.db.Connect()

        #This class will evaluate your destinations:
        self.destination = Destination()

        #Since we deal with an experiment, everything is predefine:
        self.exp_temp = Templater()
        self.exp_temp.Config( helper.get_hostconfig()['template'] )

        #Init a class to handle keyword strings:
        self.keyw = Keyword()

        #Init Rucio for later uploads and handling:
        self.rc = TransferRucio()
        self.rc.SetAccount(helper.get_hostconfig('rucio_account'))
        self.rc.rc_cli.SetConfigPath(helper.get_hostconfig("rucio_cli"))
        self.rc.rc_cli.SetProxyTicket(helper.get_hostconfig('rucio_x509'))
        self.rc.rc_cli.SetHost(helper.get_hostconfig('host'))
        self.rc.rc_cli.SetRucioAccount(helper.get_hostconfig('rucio_account'))
        self.rc.rc_cli.ConfigHost()

    def run(self,*args, **kwargs):
        self.init()
        print("-------------------------")
        print("Task: Upload by MongoDB")


        #Decide if you select runs from argsparse or just take everythin:
        if 'run_beg' in helper.global_dictionary:
            run_beg = helper.global_dictionary['run_beg']
        else:
            run_beg = self.db.GetSmallest('number')

        if 'run_end' in helper.global_dictionary:
            run_end = helper.global_dictionary['run_end']
        else:
            run_end = self.db.GetLargest('number')

        #When you found your run numbers, you want to select timestamps from them
        #This allows to catch everything in between
        print(run_beg, run_end)
        ts_beg = self.db.FindTimeStamp('number', int(run_beg) )
        ts_end = self.db.FindTimeStamp('number', int(run_end) )

        #Query an overview collection with the relevant information
        #According to the selected timestamps:
        self.db.Connect()
        self.db.SetProjection(projection={'number':True, 'name':True, '_id':True, 'start':True}, from_config=False)
        query = { '$and': [ {'start': {'$gte':ts_beg}}, {'start': {'$lte':ts_end}} ] }
        #collection = self.db.GetQuery(query, sort=[('name',-1)])
        collection = self.db.GetQuery(query)

        #Once you grabbed the overview collection, you want to
        #reset your mongodb projections to include the data field with
        #the locations:
        self.db.Connect()
        self.db.SetProjection(projection={'number':True, 'name':True, '_id':True, 'data':True, 'detector':True, 'start':True}, from_config=False)

        #Run through the overview collection:
        for i_run in collection:
            #Extract run number and name from overview collection
            r_name = i_run['name']
            r_number = i_run['number']
            #Pull the full run information (according to projection which is pre-defined) by the run name
            db_info = self.db.GetRunByName(r_name)[0]
            
            #REMOVE AND CHECK FOR UPLOAD DETECTORS (-> config files)
            if db_info['detector'] != 'tpc':
                continue
            
            for i_data in db_info['data']:

                print(r_number, helper.get_hostconfig('host'), i_data['host'] )

                #get the destination from DB:
                origin_dest = None
                if 'destination' in i_data:
                    origin_dest = i_data['destination']

                print("destination:", origin_dest)
                #print(i_data)

                dest = self.destination.EvalDestination(host=helper.get_hostconfig('host'),
                                                        origin=i_data['host'],
                                                        destination=origin_dest)

                print("get: ", dest)
                #continue
                if len(dest) == 0:
                    continue

                #reactivate if this is specified in the config file
                #if helper.global_dictionary['plugin_type'] != None and helper.global_dictionary['plugin_type'] != i_date['type']:
                    #continue

                ptr0 = None
                rlt0 = None
                rse0 = None
                for i_dest in dest:
                    if i_dest['upload'] == True:
                        ptr0 = i_dest['protocol']
                        rlt0 = i_dest['lifetime']
                        if rlt0 == "None":
                            rlt0 = None
                        rse0 = i_dest['rse']
                print("K:", ptr0, rlt0, rse0, type(ptr0), type(rlt0), type(rse0))
                #pre infos:
                origin_type     = i_data['type'] #This is the plugin name which is gonna be handled (uploaded)
                origin_location = i_data['location'] #This holds the physical location of the plugin folder
                origin_host     = i_data['host']
                origin_status   = i_data['status']

                print("Destination:", dest)
                print("Origin:", origin_type, origin_location, origin_host, origin_status)


                #Extract the template information according the pre-defined physical file structure:
                template_info = self.exp_temp.GetTemplateEval(plugin=origin_type,
                                                              host=origin_host,
                                                              string=origin_location)

                #1) Get experimental dependend rucio structure which needs to be fulfilled from the config file:
                rucio_exp_config_path = helper.get_hostconfig('rucio_template')

                rc_reader = ConfigRucioDataFormat()
                rc_reader.Config(rucio_exp_config_path)
                rucio_template = rc_reader.GetStructure()[origin_type]  #Depending on the plugin we choose the right template from the config
                rucio_template_sorted = [key for key in sorted(rucio_template.keys())]

                #Fill the key words:
                self.keyw.ResetTemplate()
                self.keyw.SetTemplate(template_info)
                self.keyw.SetTemplate(db_info)
                self.keyw.SetTemplate({'science_run':helper.get_science_run(db_info['start'])})

                for key, val, in rucio_template.items():
                    val = self.keyw.CompleteKeywords(val)


                #collect all files from the directory and do pre checks:
                if os.path.isdir(origin_location) == False:
                    print("Folder {0} does not exists, stop here".format( origin_location ))
                    continue

                #load files:
                r_path, r_folder, r_files = helper.read_folder(origin_location)

                #check if files are there:
                if len(r_files) == 0:
                    print("--> There are no files in the folder")

                #check for zero sizes fils before hand:
                for i_file in r_files:
                    f_size = os.path.getsize( os.path.join(origin_location, i_file) )
                    #print(i_file, f_size)
                    if f_size == 0:
                        print("--> file {0} is 0 bytes".format(i_file))
                        print("--> Exclude directory {0} from upload".format(origin_location))
                        continue

                #Rename:
                #This section will not be necessary anymore once we have
                #Strax ready:
                print("")
                print("--- Begin to rename")

                #create the template:
                name_old_template = "{old_name}"

                name_new_template = {}
                name_new_template['did'] = "{plugin}-{hash}-{old_name}"
                name_new_template['tag_words'] = ["plugin", "hash"]
                name_new_template = self.keyw.CompleteKeywords(name_new_template)
                name_new_template = name_new_template['did']

                for i_file in r_files:
                    #prepare file name:
                    name_old = name_old_template.format(old_name=i_file)
                    name_new = name_new_template.format(old_name=name_old)

                    name_old = os.path.join(origin_location, name_old)
                    name_new = os.path.join(origin_location, name_new)

                    #only rename it when there is no underscore in:
                    if i_file.find("-") >= 0 or len(i_file) > 13:
                        continue

                    #print("--- ",name_old, name_new)
                    os.rename(name_old, name_new)
                print("--- Renaming done")
                print("")

                #Check if rule exists in Rucio:
                rule_status = self.rc.CheckRule(upload_structure=rucio_template, rse=rse0)
                #verify the locations with rucio:
                verify_location = self.rc.VerifyLocations(upload_structure=rucio_template, upload_path=origin_location, checksum_test=False)
                #check for a database entry:
                db_dest_status = self.db.StatusDataField(db_info['_id'], type=origin_type, host=ptr0)


                print("Check:")
                print("Rucio Rules Status", rule_status)
                print("Rucio verify location", verify_location)
                print("DB status: ", db_dest_status)
                print("-----------------------")
                for tmpl_key, tmpl_val in rucio_template.items():
                    print("level", tmpl_key, tmpl_val)
                print("-----------------------")

                skip_upload = False
                if db_dest_status == False:
                    #Create a new dictionary in the data field for the plugin/host combination
                    new_data_dict={}
                    new_data_dict['location'] = "n/a"
                    new_data_dict['status'] = "transferring"
                    new_data_dict['host'] = ptr0
                    new_data_dict['type'] = origin_type
                    new_data_dict['meta'] = None
                    if ptr0 == 'rucio-catalogue':
                        new_data_dict['rse'] = []
                        new_data_dict['destination'] = []

                    self.db.AddDatafield(db_info['_id'], new_data_dict)

                elif rule_status == 'OK':
                    #if there is a rucio rule we can skip here
                    skip_upload = True
                elif db_dest_status == True and self.db.GetDataField(db_info['_id'], type=origin_type, host=ptr0, key='status') == 'transferring':
                    skip_upload = True
                elif db_dest_status == True and self.db.GetDataField(db_info['_id'], type=origin_type, host=ptr0, key='status') == 'transferred':
                    skip_upload = True

                elif db_dest_status == True and self.db.GetDataField(db_info['_id'], type=origin_type, host=ptr0, key='status') == 'RSEreupload':
                    #There is a database status found in RSEreupload state: Reset it and continoue with uploading:
                    pass
                    #self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='status', value='transferring')
                    #self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='location', value='n/a')
                elif db_dest_status == True and len(self.db.GetDataField(db_info['_id'], type=origin_type, host=ptr0, key='rse'))>0:
                    #if there exists a list of RSEs for the rucio-catalogue entry which contains the upload RSE we skip
                    list_rse_is = self.db.GetDataField(db_info['_id'], type=origin_type, host=ptr0, key='rse')
                    for i_rse_is in list_rse_is:
                        print("-", i_rse_is)
                        if i_rse_is.split(":")[0]==rse0 and i_rse_is.split(":")[1]=='OK':
                            skip_upload = True

                print("skip:", skip_upload)
                print("-- origin_type: ", origin_type)
                print("-- DB status:", self.db.GetDataField(db_info['_id'], type=origin_type, host=ptr0, key='status'))
                print("-- DB RSE: ", self.db.GetDataField(db_info['_id'], type=origin_type, host=ptr0, key='rse'))

                if skip_upload==True:
                    continue

                #if the pre checks are ok we can upload:
                self.rc.Upload(upload_structure=rucio_template, upload_path=origin_location, rse=rse0, rse_lifetime=rlt0)
#[File replicas states successfully updated]
                verify_location = self.rc.VerifyLocations(upload_structure=rucio_template, upload_path=origin_location, checksum_test=False)
                rucio_rule = self.rc.GetRule(upload_structure=rucio_template, rse=rse0)

                ##update after initial upload:
                if verify_location == True and rucio_rule['state'] == 'OK':
                    rse_to_db = ["{rse}:{state}:{lifetime}".format(rse=rse0, state=rucio_rule['state'], lifetime=rucio_rule['expires'])]
                    self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='rse', value=rse_to_db)
                    rc_location = rucio_template[rucio_template_sorted[-1]]['did']
                    self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='location', value=rc_location)
                    self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='status', value='transferred')

                    #if everything was ok we can dump the metadata.json to the runDB:
                    meta_data_file = None
                    for i_file in r_files:
                        if 'metadata.json' in i_file:
                            meta_data_file = os.path.join(origin_location, i_file)
                            break
                    if os.path.exists(meta_data_file):
                        md = json.load(open(meta_data_file))
                    else:
                        md = {}
                    self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='meta', value=md)


                    #Clean up: Remove destination field and add some rules:
                    #remove destination entry:
                    #1 find out if other destinations are on the list:
                    for i_dest in dest:
                        if i_dest['upload'] == False:
                            ptr1 = i_dest['protocol']
                            rlt1 = i_dest['lifetime']
                            rse1 = i_dest['rse']

                            #apply further rucio rules
                            if ptr1 == 'rucio-catalogue':
                                rule_list = ["{ptr}:{rse}:{rlt}".format(ptr=ptr1,
                                                                        rse=rse1,
                                                                        rlt=rlt1)]
                                print(rule_list)
                                self.rc.AddRules(upload_structure=rucio_template, rse_rules=rule_list)

                    #if we have a complete uploaded data set and further rules initiated
                    #we can reset the destintaion list
                    rucio_rule_status = []
                    rucio_rule_rse0 = self.rc.GetRule(upload_structure=rucio_template, rse=rse0)
                    rucio_rule_status.append("{rse}:{state}:{lifetime}".format(rse=rse0, state=rucio_rule_rse0['state'], lifetime=rucio_rule_rse0['expires']))
                    for i_dest in dest:
                        if i_dest['upload'] == False:
                            ptr1 = i_dest['protocol']
                            rlt1 = i_dest['lifetime']
                            rse1 = i_dest['rse']

                            #apply further rucio rules
                            if ptr1 == 'rucio-catalogue':
                                rucio_rule_rse1 = self.rc.GetRule(upload_structure=rucio_template, rse=rse1)
                                rucio_rule_status.append("{rse}:{state}:{lifetime}".format(rse=rse1, state=rucio_rule_rse1['state'], lifetime=rucio_rule_rse1['expires']))
                    print(rucio_rule_status)
                    #delete the destination if everything seems ok:
                    if rucio_rule_rse0['state'] == 'OK' and len(rucio_rule_status) == len(dest):
                        print("Destination request seems to be fulfilled")
                        print(rucio_rule_status)
                        self.db.SetDataField(db_info['_id'], type=origin_type, host=origin_host, key='destination', value=[])

                    #But also update the data field based on the latest rucio information:
                    self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='rse', value=rucio_rule_status)
                    #------Goes to memberfunctions laters

                else:
                    rse_to_db = ["{rse}:{state}:{lifetime}".format(rse=rse0, state=rucio_rule['state'], lifetime=rucio_rule['expires'])]
                    self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='rse', value=rse_to_db)
                    self.db.SetDataField(db_info['_id'], type=origin_type, host=ptr0, key='status', value='RSEreupload')

                print("TTTTTTJJJJ")
                print(rucio_rule)
                print(verify_location)
                print(db_dest_status)
                self.db.ShowDataField(db_info['_id'], type=origin_type, host=ptr0)


                for i_dest in dest:
                    if i_dest['upload'] == False:
                        ptr1 = i_dest['protocol']
                        rlt1 = i_dest['lifetime']
                        rse1 = i_dest['rse']
                        print(ptr1, rlt1, rse1)



    def __del__(self):
        print( 'Upload mongoDB stops')
