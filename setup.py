# coding=utf-8
#
# The MIT License (MIT)
#
# Copyright (c) 2016-2017 yutiansut/QUANTAXIS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import re
import sys
from pathlib import Path

from setuptools import find_packages, setup
"""
"""

# 检查Python版本 - 与QARS2对齐到3.9+
if sys.version_info < (3, 9) or sys.version_info >= (4, 0):
    print('=' * 60)
    print('错误: QUANTAXIS 2.1+ 需要 Python 3.9-3.12')
    print(f'当前版本: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')
    print('=' * 60)
    print('\n推荐使用:')
    print('  - Python 3.9+ (与QARS2 Rust核心兼容)')
    print('  - Python 3.11+ (最佳性能)')
    print('\n升级方法:')
    print('  Ubuntu/Debian: sudo apt install python3.11')
    print('  macOS: brew install python@3.11')
    print('  Windows: https://www.python.org/downloads/')
    print('=' * 60)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent

with (ROOT / 'QUANTAXIS/__init__.py').open('rt', encoding='utf8') as f:
    context = f.read()
    VERSION = re.search(r'__version__ = \'(.*?)\'', context).group(1)
    AUTHOR = re.search(r'__author__ = \'(.*?)\'', context).group(1)


def read(fname):
    return (ROOT / fname).read_text(encoding='utf8')


NAME = "quantaxis"
"""

"""
PACKAGES = find_packages(where=str(ROOT), include=["QUANTAXIS", "QUANTAXIS.*", "panda_alpha", "panda_alpha.*"])
"""

"""

DESCRIPTION = "QUANTAXIS:Quantitative Financial Strategy Framework"


# try:
#     import pypandoc
#     LONG_DESCRIPTION = pypandoc.convert_file('README.md', 'rst')
# except Exception:
# with open("README_ENG.md", "r", encoding='utf-8') as fh:
#     LONG_DESCRIPTION = fh.read()
LONG_DESCRIPTION = 'QUANTAXIS Financial Framework'

"""

"""

KEYWORDS = ["quantaxis", "quant", "finance", "Backtest", 'Framework']
"""

"""

AUTHOR_EMAIL = "yutiansut@qq.com"

URL = "https://github.com/quantaxis/quantaxis"


LICENSE = "MIT"

def read_requirements(path):
    # pip accepts inline comments; setuptools Requires-Dist accepts only PEP 508.
    # A URL's #fragment is preserved because only a whitespace-prefixed # is a comment.
    requirements = []
    for line in (ROOT / path).read_text(encoding='utf8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            requirements.append(re.split(r'\s+#', line, maxsplit=1)[0].strip())
    return list(dict.fromkeys(requirements))


INSTALL_REQUIRES = read_requirements('requirements.txt')

setup(
    name=NAME,
    version=VERSION,
    description=DESCRIPTION,
    long_description=LONG_DESCRIPTION,
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Intended Audience :: Developers',
        'Intended Audience :: Financial and Insurance Industry',
        'Operating System :: OS Independent',
        'Topic :: Office/Business :: Financial :: Investment',
    ],
    install_requires=INSTALL_REQUIRES,
    # Python版本要求 - 与QARS2对齐
    python_requires='>=3.9,<4.0',
    # 可选依赖: Rust高性能组件
    extras_require={
        'rust': [
            'qars3>=0.0.45',  # QARS2 Rust核心 (PyO3绑定)
            'qadataswap>=0.1.0',  # 跨语言零拷贝通信
        ],
        'performance': [
            'polars>=0.20.0,<0.22.0',  # 高性能数据处理
            'orjson>=3.10.0',  # 快速JSON序列化
            'msgpack>=1.1.0',  # MessagePack序列化
        ],
        'full': [
            'qars3>=0.0.45',
            'qadataswap>=0.1.0',
            'polars>=0.20.0,<0.22.0',
            'orjson>=3.10.0',
            'msgpack>=1.1.0',
            'jupyter>=1.0.0',
            'jupyterlab>=4.0.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'panda-alpha=panda_alpha.cli:main',
            'quantaxis=QUANTAXIS.QACmd:QA_cmd',
            'quantaxisq=QUANTAXIS.QAFetch.QATdx_adv:bat',
            'qarun=QUANTAXIS.QACmd.runner:run',
            'qawebserver=QUANTAXIS.QAWebServer.server:main',
        ]
    },
    keywords=KEYWORDS,
    author=AUTHOR,
    author_email=AUTHOR_EMAIL,
    url=URL,
    license=LICENSE,
    packages=PACKAGES,
    include_package_data=True,
    zip_safe=False  # 改为False，因为包含Rust扩展
)
