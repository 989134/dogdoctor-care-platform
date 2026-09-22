import unittest
from guardian.medicine_info import medicine_info


class MedicineResultTests(unittest.TestCase):
    def test_identity_strength_and_duplicate_ingredients(self):
        record={'中文品名':'測試顆粒20毫克/公克','英文品名':'EXAMPLE 20MG/GM',
                '主成分略述':'ACETYLCYSTEINE;;ACETYLCYSTEINE; acetylcysteine',
                '適應症':'減少呼吸道黏膜分泌物的粘稠性。','劑型':'口服顆粒劑','許可證字號':'測試字號'}
        info=medicine_info('',[(100,'test',record)])
        self.assertEqual(info['product_name'],record['中文品名'])
        self.assertEqual(info['strength'],'20毫克/公克')
        self.assertEqual(info['ingredients'],'ACETYLCYSTEINE')
        self.assertIn('化痰',info['purpose_plain'])
        self.assertNotIn('20',info['dosage_text'])

    def test_unknown_or_multiple_matches_do_not_get_identity(self):
        for matches in ([],[(100,'',{'中文品名':'one'}),(100,'',{'中文品名':'two'})]):
            self.assertEqual(medicine_info('未知文字',matches)['product_name'],'')

    def test_multiple_ingredients_and_other_indication_preserved(self):
        info=medicine_info('',[(100,'',{'主成分略述':'A; B;A','適應症':'原始用途文字'})])
        self.assertEqual(info['ingredients'],'A、B')
        self.assertEqual(info['purpose_plain'],'原始用途文字')


if __name__=='__main__':unittest.main()
