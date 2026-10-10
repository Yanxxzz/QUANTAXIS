#!/usr/bin/env python3
"""Migrate existing public PIT/announcement evidence; no StockDB/AKShare calls."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/".runtime"/"python"))

from panda_alpha.financial import migrate_legacy_financial, fetch_baostock_financial, migrate_eastmoney_financial, _write_batch


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",type=Path,default=ROOT.parent)
    parser.add_argument("--uri",default="mongodb://127.0.0.1:27018")
    parser.add_argument("--database",default="quantaxis")
    parser.add_argument("--report",type=Path,default=ROOT/"research_runs"/"financial_migration.json")
    parser.add_argument("--baostock-codes",nargs="*")
    parser.add_argument("--year",type=int)
    parser.add_argument("--quarter",type=int)
    parser.add_argument("--eastmoney",action="store_true")
    parser.add_argument("--start",default="2019-01-01")
    parser.add_argument("--end",default="2026-09-18")
    parser.add_argument("--cache",type=Path,default=ROOT/"research_runs"/"financial_provider_cache")
    parser.add_argument("--workers",type=int,default=4)
    parser.add_argument("--datasets",nargs="+",default=["profit","growth","balance","cash_flow"])
    args=parser.parse_args(argv)
    from pymongo import MongoClient
    client=MongoClient(args.uri,serverSelectionTimeoutMS=5000)
    client.admin.command("ping");db=client[args.database]
    if args.eastmoney:
        receipt=migrate_eastmoney_financial(db,args.cache,start=args.start,end=args.end,workers=args.workers,
                                          progress=lambda state:print(json.dumps(state,ensure_ascii=False),flush=True))
    elif args.baostock_codes:
        if not args.year or not args.quarter:parser.error("BaoStock probes require --year and --quarter")
        import baostock as bs
        login=bs.login()
        if str(login.error_code)!="0":raise RuntimeError("BaoStock login failed")
        try:
            rows=[]
            for code in args.baostock_codes:rows.extend(fetch_baostock_financial(bs,code,args.year,args.quarter,args.datasets))
            _write_batch(db,"stock_financial_provider_snapshot",rows)
            receipt={"source":"baostock","status":"provider_pubDate_snapshot_original_vintages_pending",
                     "rows":len(rows),"codes":args.baostock_codes,"year":args.year,"quarter":args.quarter,
                     "datasets":args.datasets,"full_pit_certified":False}
        finally:bs.logout()
    else:
        receipt=migrate_legacy_financial(args.workspace,db,progress=lambda state:print(json.dumps(state),flush=True))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(receipt,ensure_ascii=False,indent=2));client.close()
    return 0


if __name__=="__main__":raise SystemExit(main())
