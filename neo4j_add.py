import sys
import json
import time
import email
import email.utils
import itertools
import neo4j_conn
import Queue
import threading
from py2neo import cypher, node, rel

class neo4jLoader:
    session = None
    tx = None
    opCount = 0
    batchTarget = 1000
    namesTarget = 1000
    names = {}
    unknownMsgId = 1
    q = None
    t = None
    
    def __init__(self):
        self.session = neo4j_conn.connect()
        self.q = Queue.Queue(10000)
        self.t = threading.Thread(target=self._writer_thread)
        self.t.start()
        return
    
    def _writer_thread(self):
        bComplete = False
        while not (bComplete and self.q.empty()):
            item = self.q.get()
            
            if item[0] == 'commit' or item[0] == 'complete':
                if item[0] == 'complete':
                    bComplete = True
                    print "Completing..."
                if not self.tx is None:
                    self._write_batch()
                    self._write_names()
                    self.tx.commit()
                    self.tx = None
                    
            elif item[0] == 'name':
                msgEmail = item[1]
                msgName = item[2]
                names = self.names.setdefault(msgEmail, {})
                names[msgName] = names.get(msgName,0)+1
                
            elif item[0] == 'msg':
                print item[1]
                
            else:
                if self.tx is None:
                    self.tx = self.session.create_transaction()
                    
                self.tx.append(item[0], item[1])
                self.opCount += item[2]
                
                bCommit = False
                if self.opCount > self.batchTarget:
                    self._write_batch()
                    bCommit = True
                if len(self.names) > self.namesTarget:
                    self._write_names()
                    bCommit = True
                
                if bCommit:
                    self.tx.commit()
                    self.tx = None
                
            self.q.task_done()
            
        return
    
        
    def _append(self, cypher, props=None, opCount=0):
        item = (cypher, props, opCount)
        self.q.put(item)
        return
    
    def _write_batch(self):
        sys.stdout.write("Writing batch ("+str(self.opCount)+")... ")
        t0 = time.time()
        try:
            self.tx.execute()
        except Exception, e:
            print e
            pass
        
        # size commits so they target 1-second to write
        t1 = time.time()
        dT = t1 - t0
        if (self.opCount > self.batchTarget) or (dT > 1.0): 
            adj = (1.0 + dT) / 2
            self.batchTarget = self.opCount / adj
            if self.batchTarget < 100:
                self.batchTarget = 100
        self.opCount = 0
             
        _write_time(dT)
        return
        
    def commit(self):
        self.q.put( ('commit',None,0) )
        return

    def add(self, email_msg, emailLink=None):
        try:
            msgSubject = _get_decoded(email_msg['Subject'])
            msgDate = _get_decoded(email_msg['Date'])
            msgID = _get_decoded(email_msg['Message-ID'])
            msgIDParent = _get_decoded(email_msg['In-Reply-To'])
            date = email.utils.parsedate_tz(msgDate)
            timestamp = email.utils.mktime_tz(date)
            
            if len(msgID) == 0 or msgID == "None":
                msgID = "Unknown_%05d" % self.unknownMsgId
                self.unknownMsgId += 1
            
            msgID = msgID.strip("<>")
            msgIDParent = msgIDParent.strip("<>")
            
            # Add From Contact
            msgFrom = email.utils.getaddresses(email_msg.get_all('From', ['']))
            msgFromX = email_msg.get_all('X-From', [''])
            self._collect_name(msgFrom[0], msgFromX[0])
        
            msgEmail = str.lower(msgFrom[0][1])
            props = {"props" : { "id":msgID, "parentId":msgIDParent, "email":msgEmail, 
                                "subject":msgSubject, "date":msgDate, "timestamp":timestamp, 
                                "year":date[0], "month":date[1], "day":date[2],
                                "link":emailLink
                                }
                      }
    
            opCount = 0
            
            # Add Email
            cypher = "MERGE (e:Email {id:{props}.id}) "
            cypher += "SET e.subject={props}.subject, e.date={props}.date, e.timestamp={props}.timestamp, "
            cypher +=   "e.year={props}.year, e.month={props}.month, e.day={props}.day, "
            cypher +=   "e.link={props}.link "
            # Add From Contact
            cypher += "MERGE (a:Contact {email:{props}.email}) "
            cypher += "CREATE UNIQUE (a)-[:SENT]->(e) "
            opCount += 4
            
            # Email Thread Relation
            if msgIDParent != "None":
                cypher += "MERGE (ePar:Email {id:{props}.parentId}) "
                cypher += "CREATE UNIQUE (e)-[:INREPLYTO]->(ePar) "
                opCount += 2
            
            # Email Thread References
            refs = []
            msgRefs = email.utils.getaddresses(email_msg.get_all('References', []))
            for msgIDRef in msgRefs:
                msgIDRef = msgIDRef[1].strip("<>")
                refs.append(msgIDRef)
            if len(refs):
                props['refs'] = refs
                cypher += "FOREACH (ref in {refs} | MERGE (eRef:Email {id:ref}) CREATE UNIQUE (e)-[:REFS]->(eRef)) "
                opCount += 2*len(refs)
        
            # Add TO relations
            tos = []
            msgTo = email.utils.getaddresses(email_msg.get_all('To', ['']))
            msgToX = email_msg.get('X-To','').split('>,')
            if len(msgToX) != len(msgTo):
                msgToX = email_msg.get('X-To','').split(',')
                if len(msgToX) != len(msgTo):
                    msgToX = []
                
            for msg,msgX in itertools.izip_longest(msgTo,msgToX,fillvalue=''):
                if len(msg) > 0:
                    tos.append(str.lower(msg[1]))
                self._collect_name(msg, msgX)
            if len(tos):
                props['tos'] = tos
                cypher += "FOREACH (to IN {tos} | "
                cypher += "MERGE (aTo:Contact {email:to}) MERGE (e)-[:TO]->(aTo)) "
                opCount += 2*len(tos)
        
            # Add CC relations
            ccs = []
            msgCc = email.utils.getaddresses(email_msg.get_all('Cc', ['']))
            msgCcX = email_msg.get('X-cc','').split('>,')
            if len(msgCcX) != len(msgCc):
                msgCcX = email_msg.get('X-cc','').split(',')
                if len(msgCcX) != len(msgCc):
                    msgCcX = []
                    
            for msg,msgX in itertools.izip_longest(msgCc,msgCcX,fillvalue=''):
                if len(msg) > 0:
                    ccs.append(str.lower(msg[1]))
                self._collect_name(msg,msgX)
            if len(ccs):
                props['ccs'] = ccs
                cypher += "FOREACH (cc in {ccs} | MERGE (aCc:Contact {email:cc}) CREATE UNIQUE (e)-[:CC]->(aCc)) "
                opCount += 2*len(ccs)
            
            # Add BCC relations
            bccs = []
            msgBcc = email.utils.getaddresses(email_msg.get_all('bcc', []))
            msgBccX = email_msg.get('X-bcc','').split('>,')
            if len(msgBccX) != len(msgBcc):
                msgBccX = email_msg.get('X-bcc','').split(',')
                if len(msgBccX) != len(msgBcc):
                    msgBccX = []

            for msg,msgX in itertools.izip_longest(msgBcc,msgBccX,fillvalue=''):
                if len(msg) > 0:
                    bccs.append(str.lower(msg[1]))
                self._collect_name(msg,msgX)
            if len(bccs):
                props['bccs'] = bccs
                cypher += "FOREACH (bcc in {bccs} | MERGE (aBcc:Contact {email:bcc}) CREATE UNIQUE (e)-[:BCC]->(aBcc)) "
                opCount += 2*len(bccs)
        
            self._append(cypher, props, opCount)
            
        except Exception, e:
            print e
            pass    
    
        return
              
              
    def msg(self, msg):
        self.q.put( ('msg',msg,0) )
        return
    
    
    def complete(self):
        self.q.put( ('complete',None,0) )
        self.q.join()
        
        tx = self.session.create_transaction()
        
        sys.stdout.write("Processing Names... ")
        cypher =  "MATCH (a:Contact) WITH a "
        cypher += "MATCH (a)-[r:NAME]->() WITH a,MAX(r.count) as nmax " 
        cypher += "MATCH (a)-[r:NAME]->(n:Name) WHERE r.count = nmax "
        cypher += "SET a.name=n.name"
        tx.append(cypher)
        t0 = time.time()
        tx.execute()
        t1 = time.time()
        _write_time(t1-t0)
        
        sys.stdout.write("Processing Sent Counts... ")
        tx.append("MATCH (n:Contact)-[r:SENT]->() WITH n,count(r) AS rc SET n.sent=rc")
        tx.append("MATCH (n:Contact) WHERE NOT (n)-[:SENT]->() SET n.sent=0")
        t0 = time.time()
        tx.execute()
        t1 = time.time()
        _write_time(t1-t0)
    
        sys.stdout.write("Processing TO Counts... ")
        tx.append("MATCH (n:Contact)<-[r:TO]-() WITH n,count(r) AS rc SET n.to=rc")
        tx.append("MATCH (n:Contact) WHERE NOT (n)<-[:TO]-() SET n.to=0")
        t0 = time.time()
        tx.execute()
        t1 = time.time()
        _write_time(t1-t0)
    
        sys.stdout.write("Processing CC Counts... ")
        tx.append("MATCH (n:Contact)<-[r:CC]-() WITH n,count(r) AS rc SET n.cc=rc")
        tx.append("MATCH (n:Contact) WHERE NOT (n)<-[:CC]-() SET n.cc=0")
        t0 = time.time()
        tx.execute()
        t1 = time.time()
        _write_time(t1-t0)
    
        sys.stdout.write("Processing BCC Counts... ")
        tx.append("MATCH (n:Contact)<-[r:BCC]-() WITH n,count(r) AS rc SET n.bcc=rc")
        tx.append("MATCH (n:Contact) WHERE NOT (n)<-[:BCC]-() SET n.bcc=0")
        t0 = time.time()
        tx.commit()
        t1 = time.time()
        _write_time(t1-t0)
        return
    
    def _collect_name(self, msgAddr, msgXAddr):
        
        if msgAddr is None or len(msgAddr) == 0:
            return
        
        msgEmail = str.lower(msgAddr[1])
        
        msgName = _get_decoded(msgAddr[0])
        if len(msgName) == 0:
            msgName = _get_decoded(msgXAddr)
                
        # trim out this kinda crap </O=ENRON/OU=NA/CN=RECIPIENTS/CN=RALVARE2>
        start = msgName.find("</")
        if start != -1:
            cleanName = '' 
            while start != -1:
                end = msgName.find(">",start)
                if end != -1:
                    cleanName += msgName[:start] + '\t' + msgName[end+1:]
                else:
                    cleanName += msgName[:start] + '\t'
                start = msgName.find("</", end)
                if start != -1:
                    start = start
            msgName = cleanName

        # obvious cleanup
        msgName = msgName.lower()
        msgName = msgName.replace(msgEmail,'').replace('<>', '').replace('()', '').replace('.', ' ').replace('to:', '').replace('cc:', '')
        
        # take between leading quotes
        while len(msgName) > 2:
            end = -1
            start = msgName.find('\"')
            if start == 0:
                end = msgName.find('\"',1)
            else:
                start = msgName.find('\'')
                if start == 0:
                    end = msgName.find('\'',1)
            if end > start:
                msgName = msgName[1:end]
            else:
                break
                
        # trim cleanup
        msgName = msgName.strip(" \"',<>")
        
        # trim out between < > 
        start = msgName.find("<")
        if start != -1:
            cleanName = '' 
            while start != -1:
                end = msgName.find(">",start)
                if end != -1:
                    cleanName += msgName[:start] + msgName[end+1:]
                else:
                    cleanName += msgName[:start]
                start = msgName.find("<", end)
                if start != -1:
                    start = start
            msgName = cleanName
        
        msgName = msgName.strip(" \"',<>").title()
        msgName = " ".join(msgName.split())
        msgName = " ".join(msgName.split(", ")[::-1])

        nameLen = len(msgName)
        if (nameLen > 0) and (nameLen < 100) and not (msgName == 'None'):
            self.q.put( ('name',msgEmail,msgName) )
        elif nameLen >= 100:
            nameLen = nameLen

        return
    
    def _write_names(self):
        sys.stdout.write("Writing names ("+str(len(self.names))+")... ")
        t0 = time.time()
        
        # transform names dict to lists of lists so cypher can consume
        props = []
        opCount = 0
        for e in self.names:
            names = self.names[e]
            for name in names:
                props.append( { 'email':e, 'name':name, 'count':names[name] } )
                opCount += 1
        
        cypher = "FOREACH (item in {props} | "
        cypher +=  "MERGE (a:Contact {email:item.email}) "
        cypher +=  "MERGE (n:Name {name:item.name}) "
        cypher +=  "MERGE (a)-[r:NAME]->(n) ON CREATE SET r.count=item.count ON MATCH SET r.count=r.count+item.count) "
                 
        try:
            self.tx.append(cypher, { "props" : props })
            self.tx.execute()
            self.names.clear()
        except Exception, e:
            print e
            pass
        
        t1 = time.time()
        dT = t1 - t0
        if (opCount > self.namesTarget) or (dT > 1.0): 
            adj = (1.0 + dT) / 2
            self.namesTarget = opCount / adj
            if self.namesTarget < 100:
                self.namesTarget = 100
        
        _write_time(dT)
        return
    
    
def _get_decoded(strIn):
    strOut = strIn
    
    try:
        decVal = email.Header.decode_header(strIn)
        if not decVal[0][1] == None:
            strOut = unicode(decVal[0][0], decVal[0][1])
        else:
            strOut = unicode(decVal[0][0], "utf-8", errors='replace')
    except Exception, e:
        print e
        pass

    return strOut

def _write_time(dT):
    sys.stdout.write("    \t" + str(dT) + " seconds\n")
    return


    