import sys,unittest,tempfile
from pathlib import Path
from tkinter import ttk
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sheet_headers import parse_range,parse_pasted_region
from sheet_rows import assess_candidate_row


class CandidateRows(unittest.TestCase):
    def assess(self,matrix,header=1,start='A1',profile=None):
        bounds=parse_range(start+':'+start)
        from sheet_headers import column_name
        bounds=parse_range(f'{start}:{column_name(bounds.first_column+len(matrix[0])-1)}{bounds.first_row+len(matrix)-1}')
        return assess_candidate_row(bounds,matrix,profile or {},header)
    def test_partial_row_is_occupied(self):
        r=self.assess([['班级','姓名','学号','联系方式'],['234','','',''],['','','','']])
        self.assertEqual(r['candidate_row'],3);self.assertEqual(r['occupied_rows'],[2])
        self.assertFalse(r['can_write'])
    def test_internal_gap_is_not_append_row(self):
        r=self.assess([['姓名','学号'],['',''],['已有数据',''],['','']])
        self.assertEqual(r['candidate_row'],4)
    def test_boundary_occupied_does_not_guess_next_row(self):
        r=self.assess([['姓名','学号'],['x','']])
        self.assertIsNone(r['candidate_row']);self.assertEqual(r['status'],'empty')
    def test_only_header_requires_more_rows(self):
        self.assertIsNone(self.assess([['姓名','学号']])['candidate_row'])
    def test_whitespace_zero_and_formula_are_occupied(self):
        for value in (' ','\u200b','0','=IF(1,"","")','😀'):
            with self.subTest(value=value):
                self.assertEqual(self.assess([['姓名','学号'],[value,''],['','']])['candidate_row'],3)
    def test_unlabelled_trailing_column_data_counts_as_occupied(self):
        self.assertEqual(self.assess([['姓名','学号',''],['','','有数据'],['','','']])['candidate_row'],3)
    def test_shifted_region_coordinates(self):
        r=self.assess([['姓名','学号'],['','']],header=5,start='C5')
        self.assertEqual(r['candidate_range'],'C6:D6')
    def test_title_before_header(self):
        r=self.assess([['活动登记',''],['姓名','学号'],['','']],header=2)
        self.assertEqual(r['candidate_row'],3)
    def test_repeated_headers_stop(self):
        self.assertIsNone(self.assess([['姓名','学号'],['',''],['姓名','学号'],['','']])['candidate_row'])
    def test_duplicate_titles_and_aliases_stop(self):
        for header in (['姓名','姓名'],['姓名','学生姓名'],['座位','座位']):
            with self.subTest(header=header):self.assertIsNone(self.assess([header,['','']])['candidate_row'])
    def test_blank_gap_in_header_stops(self):
        self.assertIsNone(self.assess([['姓名','','学号'],['','','']])['candidate_row'])
    def test_formula_and_sensitive_header_stop(self):
        for title in ('=字段','密码','x'*121):
            self.assertIsNone(self.assess([['姓名',title],['','']])['candidate_row'])
    def test_unknown_headers_require_manual_selection_but_can_be_previewed(self):
        r=self.assess([['院系','宿舍'],['','']])
        self.assertEqual(r['candidate_row'],2);self.assertFalse(r['can_write'])
    def test_all_empty_cannot_be_header(self):
        self.assertIsNone(self.assess([['',''],['','']])['candidate_row'])
    def test_invalid_header_index(self):
        for value in (None,True,0,3,'1'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.assess([['姓名','学号'],['','']],header=value)
    def test_ragged_paste_cannot_infer_missing_cells(self):
        with self.assertRaisesRegex(ValueError,'列数不一致'):
            parse_pasted_region('姓名\t学号\nx',require_rectangular=True)
        # Legacy analyzer callers retain compatible parsing; UI uses strict mode.
        self.assertEqual(parse_pasted_region('姓名\t学号\nx')[1][1],['x',''])
    def test_empty_rows_with_tabs_are_retained(self):
        bounds,matrix=parse_pasted_region('姓名\t学号\n234\t\n\t\n',require_rectangular=True)
        self.assertEqual(assess_candidate_row(bounds,matrix,{},1)['candidate_row'],3)


class CandidateUI(unittest.TestCase):
    def test_requires_confirmation_and_clears_old_result(self):
        from app import App
        from sheet_header_ui import show_headers
        from test_attachment_ui import descendants
        url='https://docs.qq.com/sheet/RowsLocalTest'
        with tempfile.TemporaryDirectory() as tmp:
            root=tk.Tk();root.withdraw();app=App(root,Path(tmp));app.processing.clear()
            try:
                app.table.insert('','end',iid=url,values=('测试',));app.table.selection_set(url);show_headers(app)
                win=app.sheet_header_window;root.clipboard_clear();root.clipboard_append('姓名\t学号\n234\t\n\t\n');root.update()
                controls=list(descendants(win));buttons={w.cget('text'):w for w in controls if isinstance(w,ttk.Button)}
                buttons['粘贴范围分析'].invoke();root.update()
                labels=lambda:[str(w.cget('text')) for w in controls if isinstance(w,ttk.Label)]
                self.assertFalse(any('第3行（A3:B3）' in text for text in labels()))
                buttons['确认表头并检查空行'].invoke();root.update()
                self.assertTrue(any('第3行（A3:B3）' in text for text in labels()))
                root.clipboard_clear();root.clipboard_append('姓名\t学号\nx')
                buttons['粘贴范围分析'].invoke();root.update()
                self.assertEqual(str(buttons['确认表头并检查空行'].cget('state')),'disabled')
                self.assertFalse(any('第3行（A3:B3）' in text for text in labels()))
                win.destroy();root.update()
            finally:
                app.quit_event.set();root.destroy()
                for handler in app.logger.handlers[:]:handler.close();app.logger.removeHandler(handler)

if __name__=='__main__':unittest.main()
