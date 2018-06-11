# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import time
import base64
import requests
import logging
_logger = logging.getLogger(__name__)

from odoo.addons.Weladee_Attendances.models.grpcproto import odoo_pb2
from odoo.addons.Weladee_Attendances.models.grpcproto import weladee_pb2
from .weladee_base import stub, sync_loginfo, sync_logerror 

def sync_employee_data(emp, job_obj, department_obj, country):
    '''
    employee data to sync
    '''    
    photoBase64 = ''
    if emp.employee.photo:
        try :
            photoBase64 = base64.b64encode(requests.get(emp.employee.photo).content)
        except Exception as e:
            _logger.error("Error when load image : %s" % e)
    
    #2018-06-07 KPO don't sync note back        
    data = { "name" : ( emp.employee.first_name_english or "" ) + " " + ( emp.employee.last_name_english or "" )
            ,"employee_code" :(emp.employee.code or "" )
            ,"weladee_profile" : "https://www.weladee.com/employee/" + str(emp.employee.ID)
            ,"work_email":( emp.employee.email or "" )
            ,"first_name_english":emp.employee.first_name_english
            ,"last_name_english":emp.employee.last_name_english
            ,"first_name_thai":emp.employee.first_name_thai
            ,"last_name_thai":emp.employee.last_name_thai
            ,"nick_name_english":emp.employee.nickname_english
            ,"nick_name_thai":emp.employee.nickname_thai
            ,"active": emp.employee.Active
            ,"receive_check_notification": emp.employee.receiveCheckNotification
            ,"can_request_holiday": emp.employee.canRequestHoliday
            ,"hasToFillTimesheet": emp.employee.hasToFillTimesheet
            ,"weladee_id":emp.employee.ID
            }
    
    if emp.employee.passportNumber :
        data["passport_id"] = emp.employee.passportNumber
    if emp.employee.taxID :
        data["taxID"] = emp.employee.taxID
    if emp.employee.nationalID :
        data["nationalID"] = emp.employee.nationalID
        
    if emp.employee.positionid :
        job_datas = job_obj.search( [("weladee_id","=", emp.employee.positionid )] )
        if job_datas :
            for jdatas in job_datas :
                data[ "job_id" ] = jdatas.id
    
    if emp.DepartmentID :
        dep_datas = department_obj.search( [("weladee_id","=", emp.DepartmentID ),'|',('active','=',False),('active','=',True)] )
        if dep_datas :
            for ddatas in dep_datas :
                data[ "department_id" ] = ddatas.id

    if photoBase64:
        data["image"] = photoBase64

    if emp.Badge:
        data["barcode"] = emp.Badge

    #2018-05-29 KPO if active = false set barcode to false
    if not emp.employee.Active:
       data["barcode"] = False 

    if emp.employee.Nationality:
        if emp.employee.Nationality.lower() in country :
            data["country_id"] = country[ emp.employee.Nationality.lower() ]
    #print (emp.employee)
    if emp.employee.Phones:
       data["work_phone"] = emp.employee.Phones[0]
       #print(emp.employee.Phones)
       #print(emp.employee.Phones[0])
       
    #2018-06-01 KPO if application level >=2 > manager   
    data["manager"] = emp.employee.application_level >= 2

    return data

def sync_employee(job_obj, employee_obj, department_obj, country, authorization, return_managers, context_sync):
    '''
    sync data from employee
    '''
    try:
        context_sync['request-synced'].append('updating changes from weladee-> odoo')
        #get change data from weladee
        for weladee_emp in stub.GetEmployees(weladee_pb2.Empty(), metadata=authorization):
            if weladee_emp and weladee_emp.employee:
                #TODO: Debug
                #if weladee_emp.employee.code != 'TCO-E0001': continue

                #search in odoo
                odoo_emp_ids = employee_obj.search([("weladee_id", "=", weladee_emp.employee.ID),'|',('active','=',False),('active','=',True)])
                if not odoo_emp_ids :
                    newid = employee_obj.create( sync_employee_data(weladee_emp, job_obj, department_obj, country) ) 
                    return_managers[ newid.id ] = weladee_emp.employee.managerID

                    sync_loginfo(context_sync, "Insert employee '%s' to odoo" % weladee_emp.employee.user_name )
                else :
                    for odoo_emp_id in odoo_emp_ids :
                        odoo_emp_id.write( sync_employee_data(weladee_emp, job_obj, department_obj, country) )
                        return_managers[ odoo_emp_id.id ] = weladee_emp.employee.managerID
                        sync_loginfo(context_sync, "Updated employee '%s' to odoo" % weladee_emp.employee.user_name )
            else:
                context_sync['request-debug'].append('>weladee employee empty')            
    except:
        context_sync['request-error'] = True
        sync_logerror(context_sync, 'Error while connect to GRPC Server, please check your connection or your Weladee API Key')
        return

    #scan in odoo if there is record with no weladee_id
    context_sync['request-synced'].append('updating new changes from odoo -> weladee')
    odoo_emp_ids = employee_obj.search([('weladee_id','=',False),'|',('active','=',False),('active','=',True)])
    for odoo_emp_id in odoo_emp_ids:
        if not odoo_emp_id["weladee_id"] :

            newEmployee = odoo_pb2.EmployeeOdoo()
            newEmployee.odoo.odoo_id = odoo_emp_id.id
            newEmployee.odoo.odoo_created_on = int(time.time())
            newEmployee.odoo.odoo_synced_on = int(time.time())

            newEmployee.employee.first_name_english = odoo_emp_id.first_name_english or ''
            newEmployee.employee.last_name_english = odoo_emp_id.last_name_english or ''
            newEmployee.employee.first_name_thai = odoo_emp_id.first_name_thai or ''
            newEmployee.employee.last_name_thai = odoo_emp_id.last_name_thai or ''
            
            newEmployee.employee.email = odoo_emp_id.work_email or ''
            #2018-06-07 KPO don't sync note back
            newEmployee.employee.lg = "en"
            newEmployee.employee.Active = odoo_emp_id.active
            if odoo_emp_id.work_phone:
               newEmployee.employee.Phones[:] = [odoo_emp_id.work_phone]

            if odoo_emp_id.job_id and odoo_emp_id.job_id.weladee_id:
                newEmployee.employee.positionid = int(odoo_emp_id.job_id.weladee_id or '0')

            try:
                result = stub.AddEmployee(newEmployee, metadata=authorization)
                
                odoo_emp_id.write({'weladee_id':result.id})
                sync_loginfo(context_sync, "Added employee to weladee : %s" % odoo_emp_id.name)
            except Exception as e:
                sync_logerror(context_sync, "Add employee '%s' failed : %s" % (odoo_emp_id.name, e))
    