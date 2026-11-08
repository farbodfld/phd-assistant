# Funding and eligibility filter

Keep only positions in England (or Canada) that fully fund international students:
international tuition fees AND a stipend.

| Category | Meaning | Action |
|---|---|---|
| full_international | Advert clearly covers international fees + stipend | keep |
| competition | "Competition funded (Students Worldwide)" | keep, flag |
| unclear | Covers "fees" without saying international; "waiver"; funding not specified | keep, ask in the email, flag |
| partial | International students pay the fee difference | exclude |
| home_only | Home/UK fees only, UK or EU nationals only, CSC/Chinese only | exclude |
| outside | Scotland, Wales, Northern Ireland, outside UK/Canada | exclude |

Also exclude: deadline passed, self-funded only, wet-lab or no computing component.
Excluded positions go to "Checked and excluded" with the reason.
