import argparse
import numpy as np
import torch
from kaldi_io import read_mat_scp
from sklearn import metrics
import scipy.io as sio
import model as model_
import glob
import pickle
import os
import sys

from utils.utils import *

def prep_feats(data_, min_nb_frames=100):

	features = data_.T

	if features.shape[1]<min_nb_frames:
		mul = int(np.ceil(min_nb_frames/features.shape[1]))
		features = np.tile(features, (1, mul))
		features = features[:, :min_nb_frames]

	return torch.from_numpy(features[np.newaxis, np.newaxis, :, :]).float()

if __name__ == '__main__':

	parser = argparse.ArgumentParser(description='Evaluation')
	parser.add_argument('--test-data', type=str, default='./data/test/', metavar='Path', help='Path to input data')
	parser.add_argument('--cp-path', type=str, default=None, metavar='Path', help='Path for file containing model')
	parser.add_argument('--model', choices=['resnet_stats', 'resnet_mfcc', 'resnet_lstm', 'resnet_small', 'resnet_large', 'TDNN'], default='resnet_lstm', help='Model arch according to input type')
	parser.add_argument('--out-path', type=str, default='./', metavar='Path', help='Path for saving outputs')
	parser.add_argument('--out-prefix', type=str, default=None, metavar='Path', help='Prefix to be added to output file name')
	parser.add_argument('--no-cuda', action='store_true', default=False, help='Disables GPU use')
	parser.add_argument('--inner', action='store_true', default=True, help='Inner layer as embedding')
	parser.add_argument('--no-histogram', action='store_true', default=False, help='Disables histogram plot')
	args = parser.parse_args()
	args.cuda = True if not args.no_cuda and torch.cuda.is_available() else False

	if args.cp_path is None:
		raise ValueError('There is no checkpoint/model path. Use arg --cp-path to indicate the path!')

	print('Cuda Mode is: {}'.format(args.cuda))

	if args.cuda:
		device = get_freer_gpu()

	ckpt = torch.load(args.cp_path, map_location = lambda storage, loc: storage)

	if args.model == 'resnet_mfcc':
		model = model_.ResNet_mfcc(n_z=ckpt['latent_size'], nh=ckpt['n_hidden'], n_h=ckpt['hidden_size'], proj_size=ckpt['r_proj_size'], ncoef=ckpt['ncoef'], ndiscriminators=ckpt['ndiscriminators'])
	elif args.model == 'resnet_lstm':
		model = model_.ResNet_lstm(n_z=ckpt['latent_size'], nh=ckpt['n_hidden'], n_h=ckpt['hidden_size'], proj_size=ckpt['r_proj_size'], ncoef=ckpt['ncoef'], ndiscriminators=ckpt['ndiscriminators'])
	elif args.model == 'resnet_stats':
		model = model_.ResNet_stats(n_z=ckpt['latent_size'], nh=ckpt['n_hidden'], n_h=ckpt['hidden_size'], proj_size=ckpt['r_proj_size'], ncoef=ckpt['ncoef'], ndiscriminators=ckpt['ndiscriminators'])
	elif args.model == 'resnet_small':
		model = model_.ResNet_small(n_z=ckpt['latent_size'], nh=ckpt['n_hidden'], n_h=ckpt['hidden_size'], proj_size=ckpt['r_proj_size'], ncoef=ckpt['ncoef'], ndiscriminators=ckpt['ndiscriminators'])
	elif args.model == 'resnet_large':
		model = model_.ResNet_large(n_z=ckpt['latent_size'], nh=ckpt['n_hidden'], n_h=ckpt['hidden_size'], proj_size=ckpt['r_proj_size'], ncoef=ckpt['ncoef'], ndiscriminators=ckpt['ndiscriminators'])
	elif args.model == 'TDNN':
		model = model_.TDNN(n_z=ckpt['latent_size'], nh=ckpt['n_hidden'], n_h=ckpt['hidden_size'], proj_size=ckpt['r_proj_size'], ncoef=ckpt['ncoef'], ndiscriminators=ckpt['ndiscriminators'])


	try:
		model.load_state_dict(ckpt['model_state'], strict=True)
	except RuntimeError as err:
		print("Runtime Error: {0}".format(err))
	except:
		print("Unexpected error:", sys.exc_info()[0])
		raise

	model.eval()
	if args.cuda:
		model = model.to(device)

	test_data = None

	files_list = glob.glob(args.test_data+'*.scp')

	for file_ in files_list:
		if test_data is None:
			test_data = { k:v for k,v in read_mat_scp(file_) }
		else:
			for k,v in read_mat_scp(file_):
				test_data[k] = v

	scores_dif = []

	model.eval()

	with torch.no_grad():

		for id_ in test_data:

			enroll_utt_data = prep_feats(test_data[id_])

			if args.cuda:
				enroll_utt_data = enroll_utt_data.to(device)

			emb_enroll = model.forward(enroll_utt_data)[1].detach() if args.inner else model.forward(enroll_utt_data)[0].detach()


			pred = model.forward_bin(torch.cat([emb_enroll, emb_enroll],1))

			if model.ndiscriminators>1:
				score = torch.cat(pred, 1).mean(1).squeeze().item()
			else:
				score = pred.squeeze().item()

			scores_dif.append( score )

	print('\nScoring done')

	print('Avg: {}'.format(np.mean(scores_dif)))
	print('Std: {}'.format(np.std(scores_dif)))
	print('Median: {}'.format(np.median(scores_dif)))
	print('Max: {}'.format(np.max(scores_dif)))
	print('Min: {}'.format(np.min(scores_dif)))

	if not args.no_histogram:
		import matplotlib
		matplotlib.use('agg')
		import matplotlib.pyplot as plt
		plt.hist(scores_dif, density=True, bins=30)
		plt.savefig(args.out_path+args.out_prefix+'met_hist_vox.pdf', bbox_inches='tight')
