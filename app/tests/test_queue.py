import sys,unittest,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
import tkinter as tk
class Page:
 def __init__(self,ctx):self.ctx=ctx;self.url='about:blank';self.closed=False
 def is_closed(self):return self.closed
 def close(self):self.closed=True
 def bring_to_front(self):pass
 def goto(self,url,**kwargs):
  self.ctx.visits.append(url);self.url=url
  if url.endswith('Fail'):raise RuntimeError('test offline')
  return None
 def wait_for_timeout(self,ms):time.sleep(.01)
class Context:
 def __init__(self):self.all=[Page(self)];self.visits=[]
 @property
 def pages(self):return [p for p in self.all if not p.closed]
 def new_page(self):p=Page(self);self.all.append(p);return p
 def close(self):
  for p in self.all:p.close()
class PW:
 def __init__(self,c):self.c=c;self.chromium=self
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def launch_persistent_context(self,*a,**k):return self.c
class BrowserQueue(unittest.TestCase):
 def test_capacity_failure_and_pause(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=tk.Tk();root.withdraw();app=App(root,tmp);app.config['max_tabs']=2;app.config['max_attempts']=1
   urls=['https://docs.qq.com/form/page/'+x for x in ['Fail','One','Two','Three','Four']]
   app.store.add_many(urls);app.processing.set();context=Context()
   def wait_for(predicate):
    deadline=time.monotonic()+3
    while not predicate():
     if time.monotonic()>deadline:self.fail(str(app.store.counts()))
     time.sleep(.02)
   try:
    with patch('playwright.sync_api.sync_playwright',lambda:PW(context)),patch('app.fill_known_fields',return_value='test filled'):
     t=threading.Thread(target=app.browser);t.start()
     wait_for(lambda:app.store.counts().get('opened')==2)
     self.assertEqual(app.store.counts(),{'failed':1,'opened':2,'pending':2})
     self.assertEqual(len(context.visits),3)
     app.processing.clear()
     next(p for p in context.pages if p.url.endswith('One')).close()
     time.sleep(.08);self.assertEqual(app.store.counts().get('pending'),2)
     app.processing.set()
     wait_for(lambda:app.store.counts().get('opened')==3)
     self.assertEqual(app.store.counts().get('pending'),1)
     self.assertEqual(len(app.store.rows()),5)
     self.assertEqual(context.visits.count(urls[0]),1)
     app.quit_event.set();t.join(3);self.assertFalse(t.is_alive())
   finally:app.quit_event.set();root.update_idletasks();root.destroy()
if __name__=='__main__':unittest.main()
