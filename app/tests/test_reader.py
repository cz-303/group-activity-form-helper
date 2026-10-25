import sys,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chat_reader import find_regions,read_group_windows
from core import DEFAULTS

DEFAULTS = dict(DEFAULTS, group='测试学院2026级研究生群', 姓名='测试同学', 学号='TEST001', 班级='测试班', 学院='测试学院')
G=DEFAULTS['group']
class Node:
 def __init__(self,name='',kind='GroupControl',box=(0,0,0,0),kids=None,id=''):
  self.Name=name;self.ControlTypeName=kind;self.AutomationId=id;self.NativeWindowHandle=1
  self.BoundingRectangle=SimpleNamespace(left=box[0],top=box[1],right=box[2],bottom=box[3]);self.kids=kids or []
 def GetChildren(self):return self.kids

def window(group=G):
 header=Node(group,'ButtonControl',(260,50,480,80))
 draft=Node('draft must not be read','EditControl')
 row=Node(id='123',kids=[Node('活动 https://docs.qq.com/form/page/test','TextControl'),draft])
 ml=Node('消息列表','WindowControl',(244,95,1200,500),[Node(id='ml-root',kids=[Node(kids=[row])])])
 side=Node('会话列表','WindowControl',(50,95,240,700),[Node(G,'ButtonControl',(60,130,220,150))])
 w=Node('ChatApp','PaneControl',(0,0,1464,714),[side,header,ml,Node('private sidebar','TextControl')])
 return w,header
class Adapter(unittest.TestCase):
 def test_main_window(self):
  w,h=window();self.assertIsNotNone(find_regions(w,G))
  self.assertEqual(read_group_windows([w],G,lambda h:False),['活动 https://docs.qq.com/form/page/test'])
 def test_other_chat_with_target_in_sidebar(self):
  w,h=window('其他群');self.assertIsNone(find_regions(w,G))
  with self.assertRaisesRegex(RuntimeError,'目标群'):read_group_windows([w],G,lambda h:False)
 def test_fake_header_inside_message(self):
  w,h=window('其他群');w.kids[2].kids.append(Node(G,'ButtonControl',(260,50,480,80)))
  self.assertIsNone(find_regions(w,G))
 def test_duplicate(self):
  w,h=window()
  with self.assertRaisesRegex(RuntimeError,'多个聊天窗口'):read_group_windows([w,w],G,lambda h:False)
 def test_minimized(self):
  w,h=window()
  with self.assertRaisesRegex(RuntimeError,'最小化'):read_group_windows([w],G,lambda h:True)
 def test_changed_during_read(self):
  w,h=window();row=w.kids[2].kids[0].kids[0].kids[0];orig=row.GetChildren
  def switch():h.Name='其他群';return orig()
  row.GetChildren=switch
  with self.assertRaisesRegex(RuntimeError,'切换'):read_group_windows([w],G,lambda h:False)
if __name__=='__main__':unittest.main()
