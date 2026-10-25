import io,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from task_links import canonical_task_url
from core import SAMPLE,canonical_url,capture_notifications
from task_store import TaskStore
from attachment_store import add_image,images,get_image,associate,detach,delete_image,image_bytes,has_qr,export_image
from backup_support import make_backup,inspect_backup,stage_restore,apply_pending
from backup_validation import validate_database
from settings import validate_settings
from data_cleanup import CleanupOptions,preview_cleanup,execute_cleanup
from adapters.chat_images import capture_visible_qr,visible_image_nodes
from chat_reader import read_group_windows
from test_reader import Node,window,G

TX='https://docs.qq.com/sheet/LocalSheet'
KD='https://www.kdocs.cn/l/LocalDoc'


def qr_image():
    import zxingcpp
    code=zxingcpp.create_barcode('local-test-not-a-group',zxingcpp.BarcodeFormat.QRCode)
    view=memoryview(zxingcpp.write_barcode_to_image(code,scale=4))
    return Image.frombytes('L',(view.shape[1],view.shape[0]),view.tobytes())


class Links(unittest.TestCase):
    def test_multiple_documents_full_notice_and_form_guard(self):
        body='活动报名 🌙\n'+TX+'\n'+KD+'\n'+SAMPLE
        urls,notices=capture_notifications([body,body])
        self.assertEqual(set(urls),{TX,KD,SAMPLE})
        self.assertTrue(all(raw==body for _,raw in notices))
        self.assertIsNone(canonical_url(TX));self.assertIsNone(canonical_url(KD))
        self.assertEqual(canonical_url(SAMPLE),SAMPLE)

    def test_urls_bounded_and_exact_hosts(self):
        for url in [TX.replace('https:','http:'),TX.replace('docs.qq.com','docs.qq.com.evil.test'),
                    TX.replace('docs.qq.com','user@docs.qq.com'),TX.replace('docs.qq.com','docs.qq.com:444'),
                    TX+'\n?secret=x',TX+'?x=\x00',TX+'?x='+'a'*9000,'javascript:alert(1)']:
            self.assertIsNone(canonical_task_url(url),url[:60])
        self.assertEqual(canonical_task_url('https://kdocs.cn/l/LocalDoc/'),KD)
        self.assertEqual(canonical_task_url(TX+'?tab=second#cell=A1'),TX+'?tab=second#cell=A1')


class ImageData(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='中文图片-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.data.mkdir()
        self.store=TaskStore(self.data/'tasks.sqlite3');self.store.add_many([TX,KD])
        self.config=validate_settings({});self.image=qr_image()

    def test_local_decode_dedup_link_unlink_delete(self):
        key,new=add_image(self.store,self.image,'目标群','123',True)
        self.assertTrue(new);self.assertTrue(has_qr(get_image(self.store,key)))
        self.assertEqual(add_image(self.store,self.image),(key,False))
        self.assertEqual(len(images(self.store,pending=True)),1)
        associate(self.store,key,TX);associate(self.store,key,TX);associate(self.store,key,KD)
        self.assertEqual(images(self.store,pending=True),[])
        self.assertEqual(images(self.store,url=TX)[0]['linked'],2)
        detach(self.store,key,TX);self.assertEqual(images(self.store,url=TX),[])
        delete_image(self.store,key);self.assertEqual(images(self.store),[])
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM task_attachments').fetchone()[0],0)

    def test_non_qr_bad_images_limits_and_no_partial_insert(self):
        self.assertEqual(add_image(self.store,Image.new('RGB',(20,20)),qr_only=True),(None,False))
        for raw in [b'',b'<html>not-image</html>',b'x'*(8*1024*1024+1)]:
            with self.assertRaises(ValueError):add_image(self.store,raw)
        with patch('attachment_store.MAX_TOTAL_BYTES',10):
            with self.assertRaises(ValueError):add_image(self.store,self.image)
        with patch('attachment_store.MAX_IMAGES',0):
            with self.assertRaises(ValueError):add_image(self.store,self.image)
        self.assertEqual(images(self.store),[])

    def test_reencode_removes_metadata(self):
        from PIL.PngImagePlugin import PngInfo
        output=io.BytesIO();metadata=PngInfo();metadata.add_text('private','do-not-retain')
        self.image.save(output,format='PNG',pnginfo=metadata)
        with Image.open(io.BytesIO(image_bytes(output.getvalue()))) as saved:self.assertNotIn('private',saved.info)

    def test_backup_restore_round_trip_and_bad_image_rejected(self):
        key,_=add_image(self.store,self.image);associate(self.store,key,KD)
        archive=make_backup(self.store,self.data,self.config,'manual')
        self.assertEqual(inspect_backup(archive,validate_settings)[3],2)
        target=self.root/'恢复';target.mkdir();stage_restore(archive,target,validate_settings);apply_pending(target,validate_settings)
        restored=TaskStore(target/'tasks.sqlite3')
        self.assertEqual(get_image(restored,key),get_image(self.store,key))
        self.assertEqual(images(restored,url=KD)[0]['id'],key)
        with restored.connection() as db:db.execute('UPDATE attachments SET image=?',(b'corrupt',))
        with self.assertRaises(ValueError):validate_database(target/'tasks.sqlite3')

    def test_orphan_association_backup_rejected(self):
        with self.store.connection() as db:db.execute('INSERT INTO task_attachments VALUES(?,?)',(TX,'missing'))
        with self.assertRaises(ValueError):validate_database(self.store.path)

    def test_task_cleanup_preserves_image_pending(self):
        key,_=add_image(self.store,self.image);associate(self.store,key,TX);self.store.done([TX])
        with self.store.connection() as db:db.execute('UPDATE tasks SET updated=? WHERE url=?',(time.time()-100*86400,TX))
        plan=preview_cleanup(self.store,self.data,CleanupOptions(tasks=True,safety_backup=False))
        execute_cleanup(self.store,self.data,self.config,plan)
        self.assertIsNone(self.store.task(TX));self.assertEqual(images(self.store,pending=True)[0]['id'],key)

    def test_export_refuses_working_data_and_keeps_existing_on_failure(self):
        key,_=add_image(self.store,self.image)
        for target in [self.data/'图.png',self.root/'tasks.sqlite3']:
            with self.assertRaises(ValueError):export_image(self.store,key,target,self.data)
        target=self.root/'中文二维码.png';export_image(self.store,key,target,self.data)
        before=target.read_bytes()
        with patch('storage_io.os.replace',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):export_image(self.store,key,target,self.data)
        self.assertEqual(target.read_bytes(),before)


class QRCapture(unittest.TestCase):
    def setup_nodes(self):
        w,h=window();messages=w.kids[2];row=messages.kids[0].kids[0].kids[0]
        node=Node(kind='ImageControl',box=(300,150,500,350));node.IsOffscreen=False;row.kids.append(node)
        return w,h,messages,node

    def test_only_message_images_and_never_background(self):
        w,h,m,node=self.setup_nodes();w.kids[0].kids.append(node)
        self.assertEqual(len(list(visible_image_nodes(m))),1)
        grab=lambda box:qr_image()
        self.assertEqual(capture_visible_qr(w,m,lambda *_:False,grab)[0],[])
        self.assertEqual(len(capture_visible_qr(w,m,lambda *_:True,grab)[0]),1)
        node.IsOffscreen=True;self.assertEqual(capture_visible_qr(w,m,lambda *_:True,grab)[0],[])

    def test_capture_failure_keeps_text_but_group_switch_rejects_batch(self):
        w,h,m,node=self.setup_nodes()
        def broken(box):raise OSError('unavailable')
        self.assertEqual(capture_visible_qr(w,m,lambda *_:True,broken)[0],[])
        def callback(window,messages):h.Name='其他群'
        with self.assertRaisesRegex(RuntimeError,'切换'):read_group_windows([w],G,lambda _:False,callback)

    def test_occlusion_after_capture_discards_image(self):
        w,h,m,node=self.setup_nodes();checks=iter([True,True,False])
        self.assertEqual(capture_visible_qr(w,m,lambda *_:next(checks),lambda _:qr_image())[0],[])

    def test_setting_off_by_default_and_strict_boolean(self):
        self.assertFalse(validate_settings({})['capture_qr'])
        with self.assertRaises(ValueError):validate_settings({'capture_qr':'false'})


if __name__=='__main__':unittest.main()
