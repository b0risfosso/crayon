import string, re
from prompts import wax_worldwright_prompt

bad = []
for lit, field, fmt, conv in string.Formatter().parse(wax_worldwright_prompt):
    if field is None:
        continue
    if field != "spec_json":          # everything else is illegal
        bad.append(field)

print("Fields found:", bad or "(none)")
# Also find empty {} specifically:
print("Empty braces:", re.findall(r'(?<!{)\{\}(?!})', wax_worldwright_prompt))
