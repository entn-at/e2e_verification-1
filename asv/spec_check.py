import argparse
import numpy as np
from numpy import linalg as LA
import torch
from kaldi_io import read_mat_scp
from sklearn import metrics
import scipy.io as sio
import model as model_
import glob
import pickle
import os
import sys
import itertools
from tqdm import tqdm
from utils.utils import *
import random

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
	parser.add_argument('--trials-path', type=str, default=None, help='Path to trials file. If None, will be created from spk2utt')
	parser.add_argument('--spk2utt', type=str, default=None, metavar='Path', help='Path to spk2utt file. Will be used in case no trials file is provided')
	parser.add_argument('--cp-path', type=str, default=None, metavar='Path', help='Path for file containing model')
	parser.add_argument('--model', choices=['resnet_stats', 'resnet_mfcc', 'resnet_lstm', 'resnet_small', 'resnet_large', 'TDNN'], default='resnet_lstm', help='Model arch according to input type')
	parser.add_argument('--sample-size', type=int, default=100, metavar='N', help='Sample size (default: 100)')
	parser.add_argument('--n-repeat', type=int, default=1, metavar='N', help='Number of repetitions')
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

	if args.trials_path:
		_, utterances_list, _ = read_trials(args.trials_path)
		utterances_list = np.unique(utterances_list)
	else:
		spk2utt = read_spk2utt(args.spk2utt)
		utterances_list = []
		for spk in spk2utt:
			utterances_list.extend(spk2utt[spk])

	print('\nAll data ready. Start of scoring')

	min_eig_list = []

	for k in range(args.n_repeat):

		idx_list = random.sample(range(len(utterances_list)), min(len(utterances_list), args.sample_size))
		scores_mat = np.zeros((args.sample_size, args.sample_size))
		mem_embeddings = {}

		model.eval()

		with torch.no_grad():

			print('\nPreparing distance dictionary.')

			pairs = itertools.permutations(range(len(idx_list)), 2)
			iterator = tqdm(pairs, total=len(idx_list)**2)

			for i, j in iterator:

				anchor_utt = str(i)

				try:
					emb_anchor = mem_embeddings[anchor_utt]
				except KeyError:

					anchor_utt_data = prep_feats(test_data[utterances_list[idx_list[i]]])

					if args.cuda:
						anchor_utt_data = anchor_utt_data.to(device)

					emb_anchor = model.forward(anchor_utt_data)[1].detach() if args.inner else model.forward(anchor_utt_data)[0].detach()
					mem_embeddings[anchor_utt] = emb_anchor


				a_utt = str(j)

				try:
					emb_a = mem_embeddings[a_utt]
				except KeyError:

					a_utt_data = prep_feats(test_data[utterances_list[idx_list[j]]])

					if args.cuda:
						a_utt_data = a_utt_data.to(device)

					emb_a = model.forward(a_utt_data)[1].detach() if args.inner else model.forward(a_utt_data)[0].detach()
					mem_embeddings[a_utt] = emb_a

				scores_mat[i,j] = 1.-model.forward_bin(torch.cat([emb_anchor, emb_a],1)).squeeze().item()

			print('\nComputing scores differences.')

			w, v = LA.eig(scores_mat)
			min_eig_list.append(np.min(w))
			print('\nMinimum eigenvalue: {}'.format(min_eig_list[-1]))

	print('\nScoring done')

	if args.n_repeat>2:
		print('Avg: {}'.format(np.mean(min_eig_list)))
		print('Std: {}'.format(np.std(min_eig_list)))
		print('Median: {}'.format(np.median(min_eig_list)))
		print('Max: {}'.format(np.max(min_eig_list)))
		print('Min: {}'.format(np.min(min_eig_list)))

	if not args.no_histogram:
		import matplotlib
		matplotlib.use('agg')
		import matplotlib.pyplot as plt
		plt.hist(w, density=True, bins=30)
		plt.savefig(args.out_path+args.out_prefix+'spec_vox.pdf', bbox_inches='tight')
