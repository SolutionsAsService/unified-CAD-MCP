"""Fetch only the explicitly pinned sources for reproducible coverage checks."""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    pins=json.loads((ROOT/'sources.lock.json').read_text())['backends']
    for name,pin in pins.items():
        target=ROOT/'.cache/sources'/name
        if target.exists():
            actual=subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip()
            dirty=subprocess.check_output(['git','-C',str(target),'status','--porcelain'],text=True)
            if actual!=pin['commit'] or dirty:
                raise SystemExit(f'Refusing to change existing source checkout: {target}')
            continue
        target.mkdir(parents=True)
        for args in [ ['init',str(target)], ['-C',str(target),'remote','add','origin',pin['repository']],
                     ['-C',str(target),'fetch','--depth','1','origin',pin['commit']],
                     ['-C',str(target),'checkout','--detach',pin['commit']] ]:
            subprocess.run(['git',*args],check=True,timeout=180)

if __name__=='__main__': main()
