#!/usr/bin/env python3
"""The mechanical part of STYLE.md that this project applies, and nothing
else: braces around every control-statement body, no braced body on one
line, and the blank lines as the std library places them. Pointer spacing,
line length and the like are left as written.

    style.py [--check] [FILE ...]      default: every tracked .c and .h

Braces:
  - every if, else, for and while body is braced, on its own lines;
  - a function defined on one line is spread over lines, one statement each.
Blank lines (the shape of std):
  - none right after an opening brace, none right before a closing one, never two;
  - one after a block's closing brace, unless the next line closes another or is
    the else of this one;
  - one before a control statement that follows other code; a comment, a label
    or a NO_UNROLL right in front of the statement goes with it;
  - one between definitions at the top level.
--check: exit 1 if any file would change."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTROL = re.compile(r'^(\s*)(\}\s*else\s+if|else\s+if|if|for|while|\}\s*else|else)\b')
STARTS_CONTROL = re.compile(r'^\s*(if|for|while|do|switch)\b')
ATTACHED = re.compile(r'^\s*(/\*|//|\*|NO_UNROLL\b|\w+:;?$)')
ONE_LINE_FUNCTION = re.compile(r'^(?P<sig>[A-Za-z_][^;{}=]*\))\s*\{(?P<body>[^{}]*;)\s*\}\s*$')


def tracked_sources(arguments):
    if arguments:
        return [Path(a).resolve() for a in arguments]
    out = subprocess.check_output(['git', 'ls-files', '-z', '--', '*.c', '*.h'], cwd=ROOT)
    return [ROOT / name for name in out.decode().split('\0') if name]


def split_statements(body):
    """Top-level statements of a one-line body: split at ; outside parentheses and strings."""
    out, depth, cur, quote = [], 0, '', None
    for ch in body:
        cur += ch
        if quote:
            if ch == quote and not cur.endswith('\\' + quote):
                quote = None
            continue
        if ch in '"\'':
            quote = ch
        elif ch in '([':
            depth += 1
        elif ch in ')]':
            depth -= 1
        elif ch == ';' and depth == 0:
            out.append(cur.strip())
            cur = ''
    if cur.strip():
        out.append(cur.strip())
    return out


def spread_functions(lines):
    """A function on one line spread over lines; a function's opening brace on a
    line of its own attached to the signature."""
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = ONE_LINE_FUNCTION.match(line)
        if m and not line.startswith('#') and not line.lstrip().startswith(('if', 'for', 'while', 'else', 'do', 'switch', 'return')):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + m.group('sig').strip() + ' {')
            for stmt in split_statements(m.group('body')):
                out.append(indent + '    ' + stmt)
            out.append(indent + '}')
        elif re.match(r'^[A-Za-z_].*\)\s*$', line) and i + 1 < len(lines) and lines[i + 1] == '{':
            out.append(line.rstrip() + ' {')
            i += 1
        else:
            out.append(line)
        i += 1
    return out


def block_end(lines, i):
    """Index of the line closing the braced block that opens on lines[i]."""
    depth = 0
    for k in range(i, len(lines)):
        depth += lines[k].count('{') - lines[k].count('}')
        if depth <= 0 and k > i or depth == 0 and lines[k].rstrip().endswith('}'):
            return k
    raise ValueError('block at line %d has no end' % (i + 1))


def condition_end(lines, i, col):
    """(line index, column after the closing parenthesis) of the condition that opens at lines[i][col]."""
    depth = 0
    k, c = i, col
    while k < len(lines):
        line = lines[k]
        while c < len(line):
            ch = line[c]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return k, c + 1
            c += 1
        k += 1
        c = 0
    raise ValueError('unbalanced condition at line %d' % (i + 1))


def code_of(line):
    """The line without a trailing comment."""
    return re.sub(r'\s*(/\*.*?\*/|//.*)\s*$', '', line)


def statement_end(lines, i):
    """Index of the last line of the statement starting at lines[i]: lines until one ends in ;
    (a trailing comment aside) with parentheses balanced (a split call)."""
    depth = 0
    k = i
    while k < len(lines):
        code = code_of(lines[k])
        depth += code.count('(') - code.count(')')
        if depth <= 0 and code.rstrip().endswith(';'):
            return k
        k += 1
    raise ValueError('statement at line %d has no end' % (i + 1))


def brace_bodies(lines):
    """One pass: the first unbraced control body found gets its braces. Returns (lines, changed)."""
    for i, line in enumerate(lines):
        m = CONTROL.match(line)
        if not m or line.lstrip().startswith(('#', '/*', '//', '*')):
            continue
        indent, head = m.group(1), m.group(2)
        kind = re.sub(r'\s+', ' ', head.strip())
        if kind in ('else', '} else'):
            rest = line[m.end():].strip()
            cond_line, after = i, line[m.end():]
        else:
            paren = line.index('(', m.end() - 1) if '(' in line[m.end() - 1:] else None
            if paren is None:
                continue
            cond_line, col = condition_end(lines, i, paren)
            after = lines[cond_line][col:]
            rest = after.strip()
        if rest.startswith('{') or rest == '' and cond_line + 1 < len(lines) and lines[cond_line + 1].strip().startswith('{'):
            continue
        if rest == '':
            body_start = cond_line + 1
            nested = STARTS_CONTROL.match(lines[body_start]) or lines[body_start].lstrip().startswith('else')
            if nested and not lines[body_start].rstrip().endswith('{'):
                continue   # a nested header still without braces: it gets them first, this one on a later pass
            if nested:
                body_end = block_end(lines, body_start)
                shift = len(lines[body_start]) - len(lines[body_start].lstrip())
                body = [l[shift:] if l.startswith(' ' * shift) else l.lstrip() for l in lines[body_start:body_end + 1]]
            else:
                body_end = statement_end(lines, body_start)
                body = [l.strip() for l in lines[body_start:body_end + 1]]
            head_lines = lines[i:cond_line + 1]
            head_lines[-1] = lines[cond_line][:col].rstrip() if kind not in ('else', '} else') else line.rstrip()
            tail = body_end + 1
        else:
            if STARTS_CONTROL.match(rest) or rest.startswith('else'):
                continue
            if not code_of(rest).endswith(';'):
                continue
            body = [rest]
            head_lines = lines[i:cond_line + 1]
            head_lines[-1] = (lines[cond_line][:col] if kind not in ('else', '} else') else line[:m.end()]).rstrip()
            tail = cond_line + 1
        new = head_lines[:-1] + [head_lines[-1] + ' {'] + [indent + '    ' + b for b in body] + [indent + '}']
        # an else right after the new closing brace joins it
        if tail < len(lines) and re.match(r'^\s*else\b', lines[tail]) and lines[tail].strip() != '':
            new[-1] = indent + '} ' + lines[tail].strip()
            tail += 1
        return lines[:i] + new + lines[tail:], True
    return lines, False


def blank_lines(lines):
    out = []
    for i, line in enumerate(lines):
        if line.strip() == '':
            if not out or out[-1].strip() == '' or out[-1].rstrip().endswith('{'):
                continue   # never two, never right after an opening brace
            nxt = next((l for l in lines[i + 1:] if l.strip() != ''), '')
            if nxt.lstrip().startswith('}'):
                continue   # never right before a closing brace
        out.append(line)
    lines, out = out, []
    for i, line in enumerate(lines):
        stripped = line.strip()
        prev = out[-1] if out else ''
        if STARTS_CONTROL.match(line) and not stripped.startswith('}') and out and prev.strip() != '' \
                and not prev.rstrip().endswith('{') and not prev.lstrip().startswith('#'):
            # a control statement after code: a blank line before it, in front of its own comment or label
            k = len(out)
            while k > 0 and ATTACHED.match(out[k - 1]) and not out[k - 1].lstrip().startswith('*/'):
                k -= 1
            if k > 0 and out[k - 1].strip() != '' and not out[k - 1].rstrip().endswith('{') and not out[k - 1].lstrip().startswith('#'):
                out.insert(k, '')
        out.append(line)
        if re.match(r'^\s+\}(\s*while\s*\(.*\)\s*;)?\s*$', line) and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt.strip() != '' and not nxt.lstrip().startswith(('}', 'else', '#')):
                out.append('')
        if line.startswith('}') and i + 1 < len(lines) and lines[i + 1].strip() != '' and not lines[i + 1].startswith(('}', '#')):
            out.append('')   # one line between definitions
    return out


def format_source(text):
    lines = text.split('\n')
    lines = spread_functions(lines)
    changed = True
    rounds = 0
    while changed and rounds < 1000:
        lines, changed = brace_bodies(lines)
        rounds += 1
    lines = blank_lines(lines)
    result = '\n'.join(lines)
    return result if result.endswith('\n') else result + '\n'


def main():
    check = '--check' in sys.argv
    files = tracked_sources([a for a in sys.argv[1:] if a != '--check'])
    differ = []
    for path in files:
        before = path.read_text()
        after = format_source(before)
        if after != before:
            differ.append(path)
            if not check:
                path.write_text(after)
    if check and differ:
        print('would change: ' + ', '.join(str(p.relative_to(ROOT)) for p in differ))
        sys.exit(1)


if __name__ == '__main__':
    main()
