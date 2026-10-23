"""OCR for a cropped license plate; supports spatially detected text rows.

No fixed plate values, character positions, or fixed horizontal row split.
Requires: easyocr, opencv-python, numpy. First use downloads EasyOCR weights.
This reads a plate crop, not an entire scene with multiple vehicles.
Default profile checks common Vietnamese civilian layouts. Unknown formats
are retained as raw text; use profile="generic" for other registrations.
"""
import argparse
import json
import os
import re
import threading
from pathlib import Path

import cv2
import numpy as np

_reader = None
_lock = threading.RLock()


def normalize(text):
    """Keep observed Latin letters/digits; never substitute O/0, I/1, etc."""
    return re.sub(r'[^A-Z0-9]', '', str(text).upper())


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader


def _plausible(text, profile='vn', lines=None):
    if profile == 'generic':
        return 4 <= len(text) <= 12 and any(c.isdigit() for c in text)
    if lines and len(lines) == 2:
        return bool(re.fullmatch(r'[0-9]{2}[A-Z]{1,2}[0-9]?', lines[0]['text'])
                    and re.fullmatch(r'[0-9]{4,5}', lines[1]['text']))
    return bool(re.fullmatch(r'[0-9]{2}[A-Z]{1,2}[0-9]?[0-9]{4,5}', text))


def _group_lines(found):
    """Cluster boxes by their vertical overlap, then order each row by x.

    Heights come from text boxes, so row separation adapts to plate size.
    """
    tokens = []
    for box, raw, confidence in found:
        text = normalize(raw)
        points = np.asarray(box, dtype=float)
        if not text or points.shape != (4, 2) or not np.isfinite(points).all():
            continue
        top, bottom = float(points[:, 1].min()), float(points[:, 1].max())
        if bottom <= top or not np.isfinite(confidence):
            continue
        tokens.append({'text': text, 'raw': str(raw),
                       'confidence': float(confidence),
                       'x': float(points[:, 0].mean()),
                       'y': float(points[:, 1].mean()), 'height': bottom-top,
                       'bbox': [float(points[:, 0].min()), top,
                                float(points[:, 0].max()), bottom]})
    # Relative geometry, not a blacklist such as "VIE". Reject only small
    # side labels; preserve short/low text aligned inside the main text span.
    if len(tokens) > 1:
        dominant = max(t['height'] for t in tokens)
        anchors = [t for t in tokens if t['height'] >= 0.55 * dominant]
        left = min(t['bbox'][0] for t in anchors)
        right = max(t['bbox'][2] for t in anchors)
        tokens = [t for t in tokens if t in anchors or left <= t['x'] <= right]
    rows = []
    for token in sorted(tokens, key=lambda t: t['y']):
        matches = [row for row in rows if abs(token['y'] - np.median([t['y'] for t in row]))
                   <= 0.55 * max(token['height'], np.median([t['height'] for t in row]))]
        if matches:
            min(matches, key=lambda row: abs(token['y'] - np.median([t['y'] for t in row]))).append(token)
        else:
            rows.append([token])
    lines = []
    for row in sorted(rows, key=lambda r: np.median([t['y'] for t in r])):
        row.sort(key=lambda t: t['x'])
        count = sum(len(t['text']) for t in row)
        lines.append({'text': ''.join(t['text'] for t in row),
                      'raw_text': ' '.join(t['raw'] for t in row),
                      'confidence': sum(t['confidence'] * len(t['text']) for t in row)/count,
                      'bbox': [min(t['bbox'][0] for t in row),
                               min(t['bbox'][1] for t in row),
                               max(t['bbox'][2] for t in row),
                               max(t['bbox'][3] for t in row)]})
    return lines


def _prepare(image):
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError('Anh bien so rong hoac khong phai numpy.ndarray')
    if image.dtype != np.uint8:
        raise ValueError('Anh phai co dtype uint8 (0..255)')
    if image.ndim == 2:
        gray = image.copy()
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError('Chi ho tro anh grayscale, BGR hoac BGRA')
    h, w = gray.shape
    if min(h, w) < 2:
        raise ValueError('Anh qua nho de OCR')
    # Upsampling helps detection, but cannot restore missing source detail.
    scale = min(3.0, max(1.0, 960/w, 128/h), 1600/max(h, w))
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.copyMakeBorder(gray, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    return gray


def _reread_serial(gray, line, reader):
    """Re-read isolated glyphs only for an invalid 3/4-glyph VN serial.

    This runs OCR again on pixels, never maps '6' to 'G'. Multiple image
    variants must agree for every glyph. Original observation is retained.
    """
    observed = line['text']
    if not re.fullmatch(r'[0-9]{2}[A-Z0-9]{1,2}', observed):
        return line
    if re.fullmatch(r'[0-9]{2}[A-Z][A-Z0-9]?', observed) and line['confidence'] >= .65:
        return line
    x1, y1, x2, y2 = line['bbox']
    margin = max(3, int((y2-y1)*0.08))
    crop = gray[max(0, int(y1)-margin):min(gray.shape[0], int(y2)+margin),
                max(0, int(x1)-margin):min(gray.shape[1], int(x2)+margin)]
    if crop.size == 0:
        return line
    mask = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    # Normalize polarity for light-on-dark plates as well.
    if np.mean(mask > 0) > .5:
        mask = 255-mask
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    h, w = crop.shape
    boxes = [(x,y,cw,ch) for x,y,cw,ch,area in stats[1:]
             if .40*h <= ch <= .98*h and .02*w <= cw <= .50*w
             and area >= .008*h*w]
    boxes.sort(key=lambda b: b[0])
    if len(boxes) != len(observed):
        return line
    chars, confidences = [], []
    for i, (x,y,cw,ch) in enumerate(boxes):
        glyph = crop[max(0,y-2):y+ch+2, max(0,x-2):x+cw+2]
        glyph = cv2.copyMakeBorder(glyph, 10,10,10,10, cv2.BORDER_CONSTANT, value=255)
        glyph = cv2.resize(glyph, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        allowed = ('0123456789' if i < 2 else 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                   if i == 2 else 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
        threshold = cv2.threshold(glyph,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
        answers = []
        for variant in (glyph, threshold):
            output = reader.recognize(variant, detail=1, allowlist=allowed,
                                      decoder='beamsearch', beamWidth=5)
            options = [(normalize(text), float(score)) for _,text,score in output
                       if len(normalize(text)) == 1 and normalize(text) in allowed]
            if not options:
                return line
            answers.append(max(options, key=lambda item: item[1]))
        if answers[0][0] != answers[1][0] or min(a[1] for a in answers) < .5:
            return line
        chars.append(answers[0][0])
        confidences.append(min(a[1] for a in answers))
    # Do not rewrite the already observed province digits as a side effect.
    text = ''.join(chars)
    if text[:2] != observed[:2]:
        return line
    return {**line, 'text': text, 'confidence': min(confidences),
            'reread': True, 'observed_text': observed}


def _reread_number(gray, line, reader):
    """Reread a low-confidence number row; never truncate an extra digit."""
    if line['confidence'] >= .65:
        return line
    x1,y1,x2,y2 = line['bbox']
    margin = max(3,int((y2-y1)*.08))
    crop = gray[max(0,int(y1)-margin):min(gray.shape[0],int(y2)+margin),
                max(0,int(x1)-margin):min(gray.shape[1],int(x2)+margin)]
    if not crop.size:
        return line
    crop = cv2.copyMakeBorder(crop,10,10,10,10,cv2.BORDER_CONSTANT,value=255)
    answers = []
    for variant in (crop,cv2.threshold(crop,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]):
        found = reader.recognize(variant,detail=1,allowlist='0123456789.',
                                 decoder='beamsearch',beamWidth=5)
        if len(found) != 1:
            return line
        _, text, score = found[0]
        answers.append((normalize(text),float(score)))
    if (answers[0][0] != answers[1][0] or not re.fullmatch(r'[0-9]{4,5}',answers[0][0])
            or min(a[1] for a in answers) < .65):
        return line
    return {**line,'text':answers[0][0], 'confidence':min(a[1] for a in answers),
            'reread':True,'observed_text':line['text']}


def _component_row_candidates(gray, reader, profile, mask=None, label="components"):
    """Locate glyph rows from pixels; read full rows, never invent characters."""
    h,w = gray.shape
    if mask is None:
        mask = cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
    if np.mean(mask > 0) > .5:
        mask = 255-mask
    _,_,stats,_ = cv2.connectedComponentsWithStats(mask)
    glyphs = [(int(x),int(y),int(cw),int(ch)) for x,y,cw,ch,area in stats[1:]
              if .14*h < ch < .72*h and .008*w < cw < .22*w
              and .12 < cw/ch < 1.2 and area > .0006*w*h]
    rows=[]
    for glyph in sorted(glyphs,key=lambda b:b[1]+b[3]/2):
        x,y,cw,ch=glyph
        matches=[row for row in rows if abs((y+ch/2)-np.median([b[1]+b[3]/2 for b in row])) < .45*max(ch,np.median([b[3] for b in row]))]
        if matches:
            matches[0].append(glyph)
        else:
            rows.append([glyph])
    rows=[row for row in rows if 3 <= len(row) <= 8]
    rows.sort(key=lambda row:np.median([b[1] for b in row]))
    if len(rows) != 2:
        return []
    # Reject labels such as a small country code next to the main glyphs.
    if min(np.median([b[3] for b in row]) for row in rows) < .5*max(b[3] for row in rows for b in row):
        return []
    candidates=[]
    for variant in ('gray','otsu'):
        lines=[]
        for index,row in enumerate(rows):
            left=min(b[0] for b in row); top=min(b[1] for b in row)
            right=max(b[0]+b[2] for b in row); bottom=max(b[1]+b[3] for b in row)
            crop=gray[max(0,top-2):min(h,bottom+2),max(0,left-2):min(w,right+2)]
            if variant=='otsu':
                crop=cv2.threshold(crop,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
            if np.mean(crop) < 127:
                crop=255-crop
            crop=cv2.copyMakeBorder(crop,12,12,12,12,cv2.BORDER_CONSTANT,value=255)
            allowed='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' if index==0 else '0123456789'
            found=reader.recognize(crop,detail=1,allowlist=allowed,decoder='beamsearch',beamWidth=5)
            if len(found)!=1:
                break
            _,raw,score=found[0]
            text=normalize(raw)
            if not text:
                break
            lines.append({'text':text,'raw_text':str(raw),'confidence':float(score),
                          'bbox':[left,top,right,bottom]})
        if len(lines)!=2:
            continue
        text=''.join(line['text'] for line in lines)
        count=sum(len(line['text']) for line in lines)
        candidates.append({'text':text,'raw_text':'\n'.join(line['raw_text'] for line in lines),
            'confidence':sum(line['confidence']*len(line['text']) for line in lines)/count,
            'valid_format':_plausible(text,profile,lines),'variant':label+'_'+variant,
            'lines':lines,'observations':[]})
    return candidates


def _recognize_easyocr(image, profile="vn"):
    """Read ONE plate crop; return API-compatible results plus raw text/rows.

    Always requires human confirmation. A plausible format and repeated OCR
    agreement do not prove that the recognized registration is correct.
    """
    if profile not in ('vn', 'generic'):
        raise ValueError('profile must be vn or generic')
    gray = _prepare(image)
    variants = {
        'gray': gray,
        'clahe': cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray),
        'otsu': cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        # White text on blue/dark plates needs the opposite polarity too.
        'inverted': cv2.bitwise_not(gray),
        'adaptive': cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,31,9),
    }
    candidates = []
    with _lock:
        reader = _get_reader()
        for name, variant in variants.items():
            # Adaptive masks help glyph geometry but can flood CRAFT with noise.
            if name == 'adaptive':
                continue
            found = reader.readtext(variant, detail=1, paragraph=False,
                                    allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-',
                                    decoder='beamsearch', beamWidth=5)
            lines = _group_lines(found)
            if not lines:
                continue
            if profile == 'vn' and len(lines) == 2:
                lines[0] = _reread_serial(gray, lines[0], reader)
                lines[1] = _reread_number(gray, lines[1], reader)
            text = ''.join(line['text'] for line in lines)
            count = sum(len(line['text']) for line in lines)
            candidates.append({'text': text, 'raw_text': '\n'.join(line['raw_text'] for line in lines),
                               'confidence': sum(line['confidence']*len(line['text']) for line in lines)/count,
                               'valid_format': _plausible(text, profile, lines), 'variant': name,
                               'lines': lines,
                               'observations': [{'text': str(raw), 'confidence': float(score)}
                                                for _, raw, score in found]})
        candidates.extend(_component_row_candidates(gray, reader, profile))
        candidates.extend(_component_row_candidates(gray, reader, profile,
            mask=255-variants['adaptive'], label='adaptive_components'))
    # Agreement across preprocessing variants precedes raw confidence.
    votes = {}
    for candidate in candidates:
        votes[candidate['text']] = votes.get(candidate['text'], 0) + 1
    trusted_votes = {}
    for candidate in candidates:
        if candidate['lines'] and min(line['confidence'] for line in candidate['lines']) >= .65:
            trusted_votes[candidate['text']] = trusted_votes.get(candidate['text'], 0) + 1
    candidates.sort(key=lambda c: (c['valid_format'], trusted_votes.get(c['text'], 0), votes[c['text']], c['confidence']), reverse=True)
    winner = candidates[0] if candidates else None
    lines = winner['lines'] if winner else []
    valid = bool(winner and winner['valid_format'])
    reasons = []
    if not valid:
        reasons.append('unknown_layout' if winner else 'no_text')
    if winner and min(line['confidence'] for line in lines) < .65:
        reasons.append('low_confidence')
    if winner and votes[winner['text']] < 2:
        reasons.append('preprocessing_disagreement')
    if winner and votes[winner['text']] >= 2 and trusted_votes.get(winner['text'], 0) < 2:
        reasons.append('insufficient_confident_agreement')
    accepted = valid and not reasons
    return {
        'plate_number': winner['text'] if accepted else None,
        'suggested_plate': winner['text'] if winner else None,
        'recognition_status': 'candidate' if accepted else 'uncertain',
        'rejection_reasons': reasons,
        'confidence': round(winner['confidence'], 4) if winner else None,
        'raw_text': winner['raw_text'] if winner else '',
        'normalized_text': winner['text'] if winner else '',
        'lines': lines,
        'candidates': candidates,
        'needs_confirmation': True,
        'valid_format': valid,
        'profile': profile,
        'observations': winner.get('observations', []) if winner else [],
        'bbox_space': 'resized padded OCR image, not original camera frame',
        'format_note': 'Common VN civilian layouts only; not official verification.' if profile == 'vn' else 'Generic heuristic only.',
        'warning': 'Review against original image; OCR agreement can still be wrong.',
        'top_candidates': lines[:1] if len(lines) > 1 else [],
        'bottom_candidates': lines[1:] if len(lines) > 1 else [],
    }


_plate_reader = None


def _plate_paths():
    base=Path(__file__).resolve().parent
    model=Path(os.getenv('PLATE_OCR_MODEL','models/plate_ocr/cct_s_v2_global.onnx'))
    config=Path(os.getenv('PLATE_OCR_CONFIG','models/plate_ocr/cct_s_v2_global_plate_config.yaml'))
    return (model if model.is_absolute() else base/model,
            config if config.is_absolute() else base/config)


def _get_plate_reader():
    global _plate_reader
    if _plate_reader is None:
        from fast_plate_ocr import LicensePlateRecognizer
        import onnxruntime as ort
        model,config=_plate_paths()
        if not model.is_file() or not config.is_file():
            raise FileNotFoundError('Missing plate OCR model/config in models/plate_ocr; see README.md')
        options=ort.SessionOptions()
        options.intra_op_num_threads=2
        options.inter_op_num_threads=1
        _plate_reader=LicensePlateRecognizer(onnx_model_path=str(model),plate_config_path=str(config),
                                            device='cpu',sess_options=options)
    return _plate_reader


def _recognize_specialist(image, profile):
    # Validate input with the same public contract as the legacy reader.
    _prepare(image)
    if image.ndim==2:
        bgr=cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
    elif image.shape[2]==4:
        bgr=cv2.cvtColor(image,cv2.COLOR_BGRA2BGR)
    else:
        bgr=image
    rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
    lab=cv2.cvtColor(bgr,cv2.COLOR_BGR2LAB)
    lab[:,:,0]=cv2.createCLAHE(clipLimit=2.0,tileGridSize=(8,8)).apply(lab[:,:,0])
    contrast=cv2.cvtColor(cv2.cvtColor(lab,cv2.COLOR_LAB2BGR),cv2.COLOR_BGR2RGB)
    with _lock:
        reader=_get_plate_reader()
        inputs=[rgb,contrast]
        if reader.config.image_color_mode=='grayscale':
            inputs=[cv2.cvtColor(item,cv2.COLOR_RGB2GRAY) for item in inputs]
        predictions=reader.run(inputs,return_confidence=True,remove_pad_char=False)
    candidates=[]
    for name,prediction in zip(('rgb','local_contrast'),predictions):
        pad=reader.config.pad_char
        raw=str(prediction.plate).rstrip(pad)
        text=normalize(raw)
        # Padding probabilities are not confidence in an observed character.
        probs=np.asarray(prediction.char_probs if prediction.char_probs is not None else [],dtype=float).reshape(-1)[:len(raw)]
        finite=bool(len(probs)==len(raw) and len(probs)>0 and np.isfinite(probs).all() and ((0<=probs)&(probs<=1)).all())
        minimum=float(probs.min()) if finite else 0.0
        confidence=float(probs.mean()) if finite else 0.0
        valid=bool(raw==text and pad not in raw and _plausible(text,profile))
        candidates.append({'text':text,'raw_text':raw,'confidence':confidence,
            'min_character_confidence':minimum,'char_probs':probs.tolist() if finite else [],
            'valid_format':valid,'variant':name,
            'region':getattr(prediction,'region',None),
            'region_confidence':getattr(prediction,'region_prob',None),
            'lines':[{'text':text,'raw_text':raw,'confidence':minimum,
                      'bbox':[0,0,int(image.shape[1]),int(image.shape[0])]}] if text else [],
            'observations':[]})
    votes={}
    for candidate in candidates:
        if candidate['valid_format'] and candidate['min_character_confidence']>=.85:
            votes[candidate['text']]=votes.get(candidate['text'],0)+1
    candidates.sort(key=lambda c:(c['valid_format'],votes.get(c['text'],0),c['min_character_confidence']),reverse=True)
    winner=candidates[0] if candidates else None
    valid=bool(winner and winner['valid_format'])
    reasons=[]
    if not valid:reasons.append('unknown_layout' if winner and winner['text'] else 'no_text')
    if winner and winner['min_character_confidence']<.85:reasons.append('low_confidence')
    if winner and votes.get(winner['text'],0)<2:reasons.append('preprocessing_disagreement')
    accepted=valid and not reasons
    return {'plate_number':winner['text'] if accepted else None,
        'suggested_plate':winner['text'] if winner and valid else None,
        'recognition_status':'candidate' if accepted else 'uncertain',
        'rejection_reasons':reasons,'confidence':round(winner['confidence'],4) if winner else None,
        'min_character_confidence':round(winner['min_character_confidence'],4) if winner else None,
        'char_probs':winner['char_probs'] if winner else [],
        'raw_text':winner['raw_text'] if winner else '', 'normalized_text':winner['text'] if winner else '',
        'lines':winner['lines'] if winner else [],'candidates':candidates,
        'needs_confirmation':True,'valid_format':valid,'profile':profile,
        'engine':'plate','ocr_model':str(_plate_paths()[0]),
        'region':winner.get('region') if winner else None,
        'region_confidence':winner.get('region_confidence') if winner else None,
        'observations':[],'top_candidates':[],'bottom_candidates':[],
        'bbox_space':'whole original plate crop; no text-line localization',
        'warning':'OCR confidence is not calibrated accuracy; compare with the original image.'}


def recognize_plate(image, profile="vn", engine=None):
    """Read pixels only. Ground-truth labels and filenames never enter this API."""
    if profile not in ('vn','generic'):
        raise ValueError('profile must be vn or generic')
    engine=engine or os.getenv('OCR_ENGINE','auto')
    if engine not in ('auto','plate','easyocr'):
        raise ValueError('OCR_ENGINE must be auto, plate or easyocr')
    if engine=='auto':
        model,config=_plate_paths()
        engine='plate' if model.is_file() and config.is_file() else 'easyocr'
    if engine=='plate':
        return _recognize_specialist(image,profile)
    return {**_recognize_easyocr(image,profile),'engine':'easyocr','ocr_model':'EasyOCR english_g2'}


def render_result(image, result):
    """Build a display panel without changing the source image."""
    if image.ndim == 2:
        preview = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        preview = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    else:
        preview = image.copy()
    h, w = preview.shape[:2]
    scale = min(800 / w, 500 / h, 3)
    preview = cv2.resize(preview, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC)
    rows = [
        'Plate: ' + (result.get('plate_number') or 'NOT CONFIRMED'),
        'Confidence (OCR score): ' + (f"{result['confidence']:.3f}" if result.get('confidence') is not None else 'N/A'),
    ]
    rows += [f"Line {i}: {line['text']}" for i, line in enumerate(result.get('lines', []), 1)]
    rows += ['Status: ' + result.get('recognition_status', 'unknown')]
    if result.get('rejection_reasons'):
        rows += ['Review: ' + ', '.join(result['rejection_reasons'])]
    if not result.get('plate_number') and result.get('suggested_plate'):
        rows += ['Unverified suggestion: ' + result['suggested_plate']]
    rows += ['Raw: ' + result.get('raw_text', '').replace('\n', ' / ')]
    rows += ['Format: ' + ('layout matches - UNVERIFIED' if result.get('valid_format') else 'UNKNOWN / REVIEW'),
             'Human confirmation required - press any key to close']
    width = max(720, preview.shape[1],
                max(cv2.getTextSize(row, cv2.FONT_HERSHEY_SIMPLEX, .6, 1)[0][0] + 24 for row in rows))
    panel = np.full((preview.shape[0] + 24 + 30*len(rows), width, 3), 30, np.uint8)
    left = (width-preview.shape[1])//2
    panel[:preview.shape[0], left:left+preview.shape[1]] = preview
    for i, row in enumerate(rows):
        cv2.putText(panel, row, (12, preview.shape[0]+30+i*30),
                    cv2.FONT_HERSHEY_SIMPLEX, .6, (240, 240, 240), 1, cv2.LINE_AA)
    return panel


def main():
    parser = argparse.ArgumentParser(description='OCR mot anh bien so da crop; khong can API/YOLO.')
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, help='Luu ket qua JSON (khong ghi de file da co)')
    parser.add_argument('--show', action='store_true', help='Hien anh va ket qua OCR trong cua so')
    parser.add_argument('--save-image', type=Path, help='Luu anh ket qua PNG/JPG (khong ghi de)')
    parser.add_argument('--ocr-engine', choices=['auto','plate','easyocr'], default=None)
    parser.add_argument('--profile', choices=['vn', 'generic'], default='vn', help='vn: bien dan su VN thong dung; generic: khong rang buoc vi tri')
    args = parser.parse_args()
    try:
        data = np.fromfile(str(args.image), dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED) if data.size else None
    except OSError as exc:
        parser.error(str(exc))
    if image is None:
        parser.error(f'Khong doc duoc anh: {args.image}')
    result = recognize_plate(image, profile=args.profile, engine=args.ocr_engine)
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open('x', encoding='utf-8') as output:
            output.write(content + '\n')
    print(content)
    if args.show or args.save_image:
        panel = render_result(image, result)
        if args.save_image:
            suffix = args.save_image.suffix.lower()
            if suffix not in ('.png', '.jpg', '.jpeg'):
                parser.error('--save-image can duoi .png, .jpg hoac .jpeg')
            ok, encoded = cv2.imencode(suffix, panel)
            if not ok:
                parser.error('Khong ma hoa duoc anh ket qua')
            with args.save_image.open('xb') as output:
                output.write(encoded.tobytes())
        if args.show:
            if os.name != 'nt' and not os.getenv('DISPLAY') and not os.getenv('WAYLAND_DISPLAY') and os.sys.platform != 'darwin':
                parser.error('Khong co giao dien hien thi; dung --save-image ket_qua.png')
            try:
                cv2.imshow('License plate OCR - review result', panel)
                cv2.waitKey(0)
            except cv2.error as exc:
                parser.error(f'Khong mo duoc cua so; dung --save-image. {exc}')
            finally:
                cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
