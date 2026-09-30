#!/usr/bin/env python3
"""
@nifty の IMAP サーバから、特定ドメインの差出人のメールを一括削除する。

既定ではドライラン（件数と一覧を表示するだけ）。実際に削除するには --execute を付ける。

例:
    # まず確認（削除しない）
    python3 delete_by_domain.py -u yourname@nifty.com -d m3.com

    # 実際に削除
    python3 delete_by_domain.py -u yourname@nifty.com -d m3.com --execute

    # 全フォルダを対象に、複数ドメインを削除
    python3 delete_by_domain.py -u yourname@nifty.com -d m3.com -d example.jp --all-folders --execute

パスワードは環境変数 NIFTY_IMAP_PASSWORD、未設定なら対話入力で受け取る。
"""

import argparse
import email
import email.header
import email.utils
import getpass
import imaplib
import os
import re
import sys

DEFAULT_HOST = "imap.nifty.com"
DEFAULT_PORT = 993
FETCH_CHUNK = 200
STORE_CHUNK = 500

LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)$')


def decode_header(value):
    parts = []
    for text, charset in email.header.decode_header(value or ""):
        if isinstance(text, bytes):
            try:
                text = text.decode(charset or "ascii", errors="replace")
            except LookupError:
                text = text.decode("ascii", errors="replace")
        parts.append(text)
    return "".join(parts)


def sender_domains(from_header):
    domains = set()
    for _, addr in email.utils.getaddresses([from_header]):
        if "@" in addr:
            domains.add(addr.rsplit("@", 1)[1].strip().strip(">").lower())
    return domains


def domain_matches(domain, targets, include_subdomains):
    for t in targets:
        if domain == t or (include_subdomains and domain.endswith("." + t)):
            return True
    return False


def quote_mailbox(name):
    if name.startswith('"') and name.endswith('"'):
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def list_folders(imap):
    typ, data = imap.list()
    if typ != "OK":
        raise RuntimeError("LIST failed: %r" % data)
    folders = []
    for line in data:
        literal = None
        if isinstance(line, tuple):  # literal 形式のフォルダ名
            line, literal = line
        elif not line:
            continue
        m = LIST_RE.match(line)
        if not m:
            continue
        if b"\\noselect" in m.group("flags").lower():
            continue
        if literal is not None:
            name = literal.decode("ascii", errors="replace")
        else:
            name = m.group("name").decode("ascii", errors="replace")
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        folders.append(name)
    return folders


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def find_matches(imap, targets, include_subdomains):
    # サーバ側で FROM の部分一致検索して候補を絞り、ヘッダを見て厳密に判定する
    candidate_uids = set()
    for t in targets:
        typ, data = imap.uid("SEARCH", None, "FROM", quote_mailbox(t))
        if typ != "OK":
            raise RuntimeError("SEARCH failed: %r" % data)
        candidate_uids.update(data[0].split())
    candidate_uids = sorted(candidate_uids, key=int)

    matches = []
    for chunk in chunks(candidate_uids, FETCH_CHUNK):
        typ, data = imap.uid(
            "FETCH", b",".join(chunk).decode(),
            "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if typ != "OK":
            raise RuntimeError("FETCH failed: %r" % data)
        for item in data:
            if not isinstance(item, tuple):
                continue
            m = re.search(rb"UID (\d+)", item[0])
            if not m:
                continue
            msg = email.message_from_bytes(item[1])
            from_h = decode_header(msg.get("From", ""))
            if any(domain_matches(d, targets, include_subdomains)
                   for d in sender_domains(from_h)):
                matches.append((m.group(1), from_h,
                                decode_header(msg.get("Subject", "")),
                                msg.get("Date", "")))
    return matches


def delete_uids(imap, uids, trash):
    caps = {c.decode().upper() if isinstance(c, bytes) else c.upper()
            for c in imap.capabilities}
    for chunk in chunks(uids, STORE_CHUNK):
        uid_set = b",".join(chunk).decode()
        if trash:
            if "MOVE" in caps:
                typ, data = imap.uid("MOVE", uid_set, quote_mailbox(trash))
                if typ != "OK":
                    raise RuntimeError("MOVE failed: %r" % data)
                continue
            typ, data = imap.uid("COPY", uid_set, quote_mailbox(trash))
            if typ != "OK":
                raise RuntimeError("COPY failed: %r" % data)
        typ, data = imap.uid("STORE", uid_set, "+FLAGS.SILENT", r"(\Deleted)")
        if typ != "OK":
            raise RuntimeError("STORE failed: %r" % data)
        if "UIDPLUS" in caps:
            # 対象以外の \Deleted 付きメールを巻き込まない
            imap.uid("EXPUNGE", uid_set)
    if "UIDPLUS" not in caps:
        imap.expunge()


def main():
    p = argparse.ArgumentParser(
        description="@nifty IMAP から特定ドメインの差出人のメールを一括削除")
    p.add_argument("-u", "--user", required=True,
                   help="IMAP ユーザ名（@nifty のメールアドレス等）")
    p.add_argument("-d", "--domain", action="append", required=True,
                   help="削除対象の差出人ドメイン（複数指定可）例: m3.com")
    p.add_argument("-f", "--folder", action="append",
                   help="対象フォルダ（複数指定可、既定: INBOX）")
    p.add_argument("--all-folders", action="store_true",
                   help="全フォルダを対象にする（--trash のフォルダは除外）")
    p.add_argument("--exact", action="store_true",
                   help="サブドメイン（例: mail.m3.com）を対象外にする")
    p.add_argument("--trash", metavar="FOLDER",
                   help="完全削除せずこのフォルダへ移動する")
    p.add_argument("--execute", action="store_true",
                   help="実際に削除する（指定しなければドライラン）")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("-q", "--quiet", action="store_true",
                   help="メールごとの一覧を表示しない")
    args = p.parse_args()

    targets = [d.lower().lstrip("@").strip() for d in args.domain]
    password = os.environ.get("NIFTY_IMAP_PASSWORD") or getpass.getpass(
        "IMAP password for %s: " % args.user)

    imap = imaplib.IMAP4_SSL(args.host, args.port)
    try:
        imap.login(args.user, password)
    except imaplib.IMAP4.error as e:
        sys.exit("ログインに失敗しました: %s" % e)

    try:
        if args.all_folders:
            folders = [f for f in list_folders(imap) if f != args.trash]
        else:
            folders = args.folder or ["INBOX"]

        total = 0
        for folder in folders:
            typ, data = imap.select(quote_mailbox(folder),
                                    readonly=not args.execute)
            if typ != "OK":
                print("[skip] %s を開けません: %r" % (folder, data),
                      file=sys.stderr)
                continue
            matches = find_matches(imap, targets, not args.exact)
            print("== %s: %d 件" % (folder, len(matches)))
            if not args.quiet:
                for uid, frm, subj, date in matches:
                    print("  [%s] %s | %s | %s" % (uid.decode(), date, frm, subj))
            if matches and args.execute:
                delete_uids(imap, [m[0] for m in matches], args.trash)
                print("   -> %s しました" % (
                    "%s へ移動" % args.trash if args.trash else "削除"))
            total += len(matches)
            imap.close()

        if args.execute:
            print("合計 %d 件を処理しました。" % total)
        else:
            print("合計 %d 件が対象です（ドライラン）。削除するには --execute を付けて再実行してください。" % total)
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
