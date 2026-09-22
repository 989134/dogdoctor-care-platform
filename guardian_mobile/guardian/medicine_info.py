"""Extract labelled package text without inferring a patient's prescription."""
import re

def clean_ingredients(value):
    unique={}
    for part in re.split(r'[;；\n]+',value or ''):
        part=part.strip()
        if part:unique.setdefault(part.casefold(),part)
    return '、'.join(unique.values())

def plain_purpose(indication):
    normalized=re.sub(r'[。．.\s]','',indication).replace('粘','黏')
    if normalized=='減少呼吸道黏膜分泌物的黏稠性':
        return '化痰用藥：降低呼吸道痰液的黏稠度。'
    return indication

def medicine_info(text, matches):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    expiry=[line for line in lines if re.search(r'有效期限|保存期限|到期|效期|\bEXP(?:IRY|IRATION)?\b',line,re.I) and '許可證' not in line]
    dosage=[line for line in lines if re.search(r'每次|每日|每天|每晚|服用|飯前|飯後|睡前|用法|用量',line)]
    record=matches[0][2] if len(matches)==1 else {}
    name=record.get('中文品名','') or record.get('英文品名','')
    units=r'(?:mcg|mg|gm|ml|g|%|毫克|微克|公克|毫升)'
    strengths=re.findall(r'\d+(?:\.\d+)?\s*'+units+r'(?:\s*/\s*(?:\d+(?:\.\d+)?\s*)?'+units+r')?', name,re.I)
    indication=record.get('適應症','')
    return dict(
        product_name=name,
        english_name=record.get('英文品名',''),
        strength=' ／ '.join(dict.fromkeys(strengths)),
        license=record.get('許可證字號',''),
        manufacturer=record.get('製造廠名稱','') or record.get('申請商名稱',''),
        purpose_plain=plain_purpose(indication),
        expiry_text='\n'.join(expiry) or '未辨識到包裝效期，請補拍 EXP／有效期限。',
        dosage_text='\n'.join(dosage) or '未辨識到個人服用量，請補拍藥袋用法。',
        indication=record.get('適應症') or '尚無可確認的用途資料。',
        ingredients=clean_ingredients(record.get('主成分略述')) or '尚無資料',
        form=record.get('劑型') or '尚無資料',
        category=record.get('藥品類別') or '尚無資料',
        source='提供的 36_1.xml 許可證資料（非即時更新）',
        warning='效期與服用量是文字候選，請逐字核對原包裝／藥袋；許可證有效日期不是這盒藥的到期日。用途是登錄適應症，不代表個人診斷或服用建議。')
