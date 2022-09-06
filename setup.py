from setuptools import setup, find_packages

setup(
	name='aloception',
	author='Visual Behavior',
	version='0.3.0',
	description='Aloception is a set of package for computer vision: aloscene, alodataset, alonet.',
	packages=find_packages(include=['aloscene', 'alodataset', 'alonet']),
	url='https://github.com/Visual-Behavior/aloception',
	install_requires=[
		'numpy',
		'opencv-python',
		'torch',
		'torchvision',
		'matplotlib',
		'Pillow',
		'more-itertools',
		'tensorflow',
		'waymo-open-dataset',
		'pytorch-lightning',
		'pycocotools',
		'click',
		'nvidia-tensorrt',
		'nvidia-pyindex',
		'onnx',
		'onnxsim',
		'pycuda',
		'wandb'
	],
	license_files=['LICENSE'],
	keywords=['artificial intelligence', 'computer vision'],
	classifiers=[
		'Programming Language :: Python',
		'Topic :: Scientific/Engineering :: Artificial Intelligence'
	]
)
