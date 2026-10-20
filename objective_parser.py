"""Strict objective-PDF parsing and deterministic marking. No network or database."""
import re
from decimal import Decimal, ROUND_HALF_UP

def parse_objectives(text):
    if len(text)>300000:raise ValueError('PDF text exceeds the supported size.')
    sections=re.split(r'(?im)^\s*(?:answer\s*key|answers)\s*:?[ \t]*$',text,maxsplit=1)
    text=sections[0];key={}
    if len(sections)>1:
        pairs=re.findall(r'(?i)(\d{1,3})\s*[.):-]?\s*([a-e])\b',sections[1])
        for number,letter in pairs:
            if number in key:raise ValueError('Duplicate question number in the answer key.')
            key[number]=letter.lower()
    heads=list(re.finditer(r'(?im)^\s*(?:question\s+)?(\d{1,3})[.)]\s+',text))
    if not 1<=len(heads)<=100:raise ValueError('Use 1–100 numbered objective questions, each beginning on a new line.')
    questions=[];seen=set()
    for i,h in enumerate(heads):
        number=h.group(1)
        if number in seen:raise ValueError('Question numbers must be unique.')
        seen.add(number)
        chunk=text[h.end():heads[i+1].start() if i+1<len(heads) else len(text)].strip()
        inline=re.search(r'(?im)^\s*(?:answer|ans|correct answer)\s*:\s*([a-e])\s*$',chunk)
        answer=key.get(number)
        if inline:
            given=inline.group(1).lower()
            if answer and given!=answer:raise ValueError('Conflicting inline and final answer keys.')
            answer=given;chunk=chunk[:inline.start()]+chunk[inline.end():]
        matches=list(re.finditer(r'(?m)(?:^|[ \t]+)\(?([A-Ea-e])[.)][ \t]+',chunk))
        letters=[m.group(1).lower() for m in matches]
        if letters not in [list('abcd'),list('abcde')]:raise ValueError(f'Question {number}: provide clear options A–D or A–E, in order.')
        stem=chunk[:matches[0].start()].strip()
        options={m.group(1).lower():chunk[m.end():matches[j+1].start() if j+1<len(matches) else len(chunk)].strip() for j,m in enumerate(matches)}
        if len(stem)<3 or any(not v for v in options.values()):raise ValueError(f'Question {number}: question or option is blank.')
        if answer and answer not in options:raise ValueError(f'Question {number}: answer is not a valid option.')
        questions.append({'id':f'q{number}','text':stem,'options':options,'correctAnswer':answer or '', 'explanation':'Answer supplied in the teacher’s PDF.' if answer else '', 'needs_review':not bool(answer),'key_source':'teacher_pdf' if answer else 'pending','max_marks':1})
    if set(key)-seen:raise ValueError('The answer key refers to missing questions.')
    return questions
