import time
from minezy_api import app
from minezy_api import neo4j_conn
from query_common import prepare_date_range, prepare_date_clause


def query_contacts(account, params, countResults=False):

    t0 = time.time()

    if params['rel'] == 'SENDER':
        relL = 'SENT'
        relR = 'TO|CC|BCC'
    elif params['rel'] == 'RECEIVER':
        relL = 'TO|CC|BCC'
        relR = 'SENT'
    else:
        relL = 'SENT|TO|CC|BCC'
        relR = 'SENT|TO|CC|BCC'

    params['ymd'],bYear,bMonth,bDay = prepare_date_range(params)

    bWith = False
    bWhere = True
    query_str = ''
    if len(params['left']) or len(params['right']):
        
        if len(params['left']) and len(params['right']):
            query_str = "MATCH (cL:{0}Contact)-[rL:"+relL+"]-(e:{0}Email)-[rR:"+relR+"]-(cR:{0}Contact),(e)--(n:{0}Contact) "
            query_str += "WHERE cL.email IN {{left}} AND cR.email IN {{right}} "
            query_str += "AND (type(rL)='SENT' OR type(rR)='SENT') "
            query_str += "AND NOT (n.email IN {{right}} OR n.email IN {{left}}) "
            
        elif len(params['left']):
            query_str = "MATCH (m:{0}Contact)-[rL:"+relL+"]-(e:{0}Email)-[rR:"+relR+"]-(n:{0}Contact) "
            query_str += "WHERE m.email IN {{left}} "
            query_str += "AND (type(rL)='SENT' OR type(rR)='SENT') "
            
        else:
            query_str = "MATCH (m:{0}Contact)-[rL:"+relL+"]-(e:{0}Email)-[rR:"+relR+"]-(n:{0}Contact) "
            query_str += "WHERE m.email IN {{right}} "
            query_str += "AND (type(rL)='SENT' OR type(rR)='SENT') "
            
        if len(params['ymd']):
            query_str += "WITH n,e MATCH "
            query_str += prepare_date_clause(bYear, bMonth, bDay)
            
    elif len(params['ymd']):
        query_str = "MATCH (n:{0}Contact)-[r:"+relL+"]-(e:{0}Email)"
        query_str += prepare_date_clause(bYear, bMonth, bDay, bNode=False)
        
    else:
        bWith = True
        
        count = relL.split('|')
        query_str = "MATCH (n:{0}Contact) "
        for i,cnt in enumerate(count):
            if i == 0:
                query_str += "WITH n,"
            else:
                query_str += "+"
            if cnt == 'SENT':
                query_str += "n.sent"
            elif cnt == 'TO':
                query_str += "n.to"
            elif cnt == 'CC':
                query_str += "n.cc"
            elif cnt == 'BCC':
                query_str += "n.bcc"
        query_str += " AS count WHERE count > 0 "

    if params['keyword']:
        if not bWhere:
            query_str += "WHERE "
        else:
            query_str += "AND "
        query_str += "n.name =~ '(?i).*"+params['keyword']+".*' "
        query_str += "OR "
        query_str += "n.email =~ '(?i).*"+params['keyword']+".*' "

    if not bWith:
        query_str += "WITH n,count(distinct(e)) as count "
        
    query_str += "RETURN COALESCE(n.name,n.email) as name,n.email,count ORDER BY count " + params['order'] + ", name ASC"

    if params['index'] or params['page'] > 1:
        query_str += " SKIP "+ str(params['index'] + ((params['page']-1)*params['limit']))
    if params['limit']:
        query_str += " LIMIT {{limit}}"

    # Apply this query to given account only
    accLbl = ""
    if account is not None:
        accLbl = "`%d`:" % account
    query_str = query_str.format(accLbl)
    
    if countResults:
        resp = _query_count(query_str, params)
    else:
        if app.debug:
            print query_str

        tx = neo4j_conn.g_session.create_transaction()
        tx.append(query_str, params)
        results = tx.commit()

        contacts = []
        count = -1
        for count,record in enumerate(results[0]):
            contact = {
                'name':  record[0],
                'email': record[1],
                'count': record[2]
                }

            contacts.append(contact)

        resp = {}
        resp['_count'] = count+1
        resp['_params'] = params
        resp['_query'] = query_str
        resp['contact'] = contacts

    t1 = time.time()
    resp['_query_time'] = t1-t0

    return resp


def _query_count(query_str, params):
    count_str = query_str[0:query_str.find("RETURN")]
    count_str += "RETURN count(*)"

    if app.debug:
        print count_str

    tx = neo4j_conn.g_session.create_transaction()
    tx.append(count_str, params)
    results = tx.commit()

    resp = {'count': results[0][0][0] }
    resp['_params'] = params
    resp['_query'] = count_str
    return resp


    