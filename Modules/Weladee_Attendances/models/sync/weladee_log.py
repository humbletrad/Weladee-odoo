# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import time
import datetime

import odoo
from odoo.http import request
from odoo.addons.Weladee_Attendances.models.grpcproto import odoo_pb2
from odoo.addons.Weladee_Attendances.models.grpcproto import weladee_pb2
from .weladee_base import stub, myrequest, sync_loginfo, sync_logerror, sync_logdebug, sync_logwarn, sync_stop, sync_weladee_error,sync_clean_up
from .weladee_base import sync_stat_to_sync,sync_stat_create,sync_stat_update,sync_stat_error,sync_stat_info 
from odoo.addons.Weladee_Attendances.library.weladee_lib import _convert_to_tz_time

def _compare_2_date(date1, date2, comparer):
    '''
    compare
    '''
    d_date1 = datetime.datetime.strptime(date1,'%Y-%m-%d %H:%M:%S')
    d_date2 = datetime.datetime.strptime(date2,'%Y-%m-%d %H:%M:%S')
    if comparer == '>':
       return d_date1 > d_date2
    elif comparer == '<':
       return d_date1 < d_date2 

def sync_log_data(emp_obj, att_obj, weladee_att, odoo_weladee_ids, context_sync):
    '''
    sync log data from weladee to odoo data

    1 odoo record will link 2 weladee logevent

    '''
    date = datetime.datetime.fromtimestamp(weladee_att.logevent.timestamp).strftime('%Y-%m-%d %H:%M:%S')
    data = {'employee_id': odoo_weladee_ids.get(str(weladee_att.logevent.employeeid),False)}
   
    # look if there is odoo record with same time
    # if not found then create else update    
    check_field = 'check_in'
    if weladee_att.logevent.action == "o" : 
        check_field = 'check_out' 

    data['res-mode'] = 'create'
    #write checkin/out time
    data[check_field] = date

    if data['res-mode'] == 'create':
       if check_field == 'check_in': 
          prev_rec = att_obj.search( [ ('employee_id','=', data['employee_id'] ), (check_field,'=', date)],limit=1 )
          if prev_rec and prev_rec.id:
             data['res-mode'] = '' 
             sync_logdebug(context_sync, 'weladee > %s ' % weladee_att)
             sync_logdebug(context_sync, 'odoo > %s ' % data)
             sync_logwarn(context_sync, 'this checkin record already exist for this %s exist, no change ' % data['employee_id'])

       elif check_field == 'check_out':
            prev_rec = att_obj.search( [ ('employee_id','=', data['employee_id'] ), ('check_out','=', False)],limit=1)
            if prev_rec and prev_rec.id:
                data['res-mode'] = 'update' 
                data['res-id'] = prev_rec.id
            else:
                data['res-mode'] = '' 
                sync_logdebug(context_sync, 'weladee > %s ' % weladee_att)
                sync_logdebug(context_sync, 'odoo > %s ' % data)
                sync_logwarn(context_sync, 'can''t find this odoo-employee-id %s with no checkout ' % data['employee_id'])
    return data      

def get_emp_odoo_weladee_ids(emp_obj, odoo_weladee_ids):
    '''
    return odoo id from weladee id
    '''
    odoo_weladee_ids = {}
    for each in emp_obj.search([('weladee_id','!=',False),'|',('active','=',False),('active','=',True)]):
        odoo_weladee_ids[each.weladee_id] = each.id

    return odoo_weladee_ids    

def create_odoo_log(self, data, context_sync,weladee_log):    
    '''
    create odoo with new connection to be able to continue
    '''
    ret = False
    try:
        ret = self.env['hr.attendance'].create(sync_clean_up(data))
    except Exception as e:
        pass
    return ret

def update_odoo_log(self, odoo_log, data, context_sync,weladee_log):    
    '''
    update odoo with new connection to be able to continue
    '''
    try:
        return self.env['hr.attendance'].browse(odoo_log.id).write(sync_clean_up(data))
    except Exception as e:
        return False

def update_odoo_orm_log(self, id, sql, context_sync):    
    '''
    update odoo without orm, with new connection to be able to continue
    '''
    ret = False
    cr = False
    try:
        #ret = self.env['hr.attendance'].browse(id).write(data)
        if not context_sync['cursor-cr']:
           cr = odoo.registry(self._cr.dbname).cursor()

        context_sync['cursor-cr'].execute('''
        update hr_attendance set %s where id = %s
        ''' % (sql, id )) 
        context_sync['cursor-cr'].commit()

    except Exception as e:

        sync_logdebug(context_sync, 'odoo > %s' % sql)         
        sync_logerror(context_sync,'error when updated in odoo: %s' % e)
    
    return ret

def sync_delete_log(self, context_sync, period_settings):
    '''
    delete the hr attendance according filter
    '''
    dt_today = datetime.datetime.today()
    dt_unit = int(period_settings["unit"])
    dt_from = False
    if period_settings["period"] == "w":
       dt_from = dt_today - datetime.timedelta(days=(dt_unit * 7))
    elif period_settings["period"] == "m":       
       dt_from = dt_today.replace(month=dt_today.month - dt_unit) 
    elif period_settings["period"] == "y":       
       dt_from = dt_today.replace(month=dt_today.year - dt_unit)             

    dt_from_utc = False
    dt_delete_msg = ''
    if period_settings["period"] == "all":
        del_ids = self.env['hr.attendance'].search([])
        dt_delete_msg = 'remove all %s attendance(s) from all records' % len(del_ids)
    else:
       # delete every record that has checkin after the select period
       dt_from_utc = _convert_to_tz_time(self, dt_from.strftime('%Y-%m-%d 00:00:00'))
       del_ids = self.env['hr.attendance'].search([('check_in','>=', dt_from_utc.strftime('%Y-%m-%d 00:00:00'))])
       dt_delete_msg = 'remove all %s attendance after this period (%s)' % (len(del_ids),dt_from.strftime('%Y-%m-%d 00:00:00'))

    if del_ids: 
       del_ids.unlink()
       sync_logwarn(context_sync, dt_delete_msg)

    return dt_from_utc 

def sync_log(self, emp_obj, att_obj, authorization, context_sync, odoo_weladee_ids, period_settings):
    '''
    sync all log from weladee

    '''
    context_sync['stat-log'] = {'to-sync':0, "create":0, "update": 0, "error":0}
    context_sync['error-emp'] = {}
    context_sync['cursor'] = False
    
    dt_from_utc = sync_delete_log(self, context_sync, period_settings)

    odoo_att = False
    weladee_att = False
    iteratorAttendance = []
    try:
        sync_loginfo(context_sync,'[log] updating changes from weladee-> odoo')
        req = odoo_pb2.AttendanceRequest()
        if dt_from_utc:
           req.From = int(dt_from_utc.timestamp())
        #print(req)
        ireq = 0
        for weladee_att in stub.GetNewAttendance(req, metadata=authorization):
            ireq +=1
            #print('%s %s'% (ireq, weladee_att))
            sync_stat_to_sync(context_sync['stat-log'], 1)
            if not weladee_att :
                sync_logwarn(context_sync,'weladee attendance is empty')
                continue

            #if empty, create one 
            if not odoo_weladee_ids: 
                sync_logdebug(context_sync, 'getting all employee-weladee link') 
                odoo_weladee_ids = get_emp_odoo_weladee_ids(emp_obj, odoo_weladee_ids)
            
            # this function should write enough, odoo and weladee current record data
            odoo_att = sync_log_data(emp_obj, att_obj, weladee_att, odoo_weladee_ids, context_sync)
            
            if odoo_att and odoo_att['res-mode'] == 'create':
                newid = create_odoo_log(self,odoo_att,context_sync,weladee_att)                
                
                if newid and newid.id:
                    sync_logdebug(context_sync, "Insert log '%s' to odoo" % odoo_att )
                    sync_stat_create(context_sync['stat-log'], 1)

                    #_add_weladee_log_back(newid.id,weladee_att,iteratorAttendance)
                else:
                    #sync_logdebug(context_sync, 'weladee > %s' % weladee_att) 
                    #sync_logerror(context_sync, "error while create odoo log id %s of '%s' in odoo" % (odoo_att.get('res-id',False), odoo_att) ) 
                    sync_stat_error(context_sync['stat-log'], 1)
            elif odoo_att and odoo_att['res-mode'] == 'update':
                odoo_id = att_obj.search([('id','=',odoo_att.get('res-id',False) )])
                if odoo_id.id:                    
                    if update_odoo_log(self, odoo_id,odoo_att, context_sync,weladee_att):
                        sync_logdebug(context_sync, "Updated log '%s' to odoo" % odoo_att )
                        sync_stat_update(context_sync['stat-log'], 1)

                        #_add_weladee_log_back(odoo_id.id,weladee_att,iteratorAttendance)                         
                    else:
                        #sync_logdebug(context_sync, 'odoo > %s' % odoo_att) 
                        #sync_logerror(context_sync, "error found while update this odoo log id %s" % odoo_att.get('res-id',False) )
                        sync_stat_error(context_sync['stat-log'], 1)

                else:
                    #sync_logdebug(context_sync, 'weladee > %s' % weladee_att) 
                    #sync_logdebug(context_sync, 'odoo > %s' % odoo_att) 
                    sync_logerror(context_sync, "Not found this odoo log id %s of '%s' in odoo" % (odoo_att.get('res-id',False) , odoo_att) ) 
                    sync_stat_error(context_sync['stat-log'], 1)

    except Exception as e:
        sync_logdebug(context_sync, 'weladee >> %s' % weladee_att or '-') 
        sync_logdebug(context_sync, 'odoo >> %s' % odoo_att or '-') 
        if sync_weladee_error(weladee_att, 'log', e, context_sync):
           return
    #stat
    del context_sync['cursor']
    sync_stat_info(context_sync,'stat-log','[log] updating changes from weladee-> odoo')