from setuptools import setup, find_packages

setup(
    name="tui_code_editor",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        'prompt_toolkit>=3.1',
        'Pygments',
    ],
)
