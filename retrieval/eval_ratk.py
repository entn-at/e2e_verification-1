from __future__ import print_function
import argparse
import torch
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
from models import vgg, resnet, densenet
import numpy as np
import os
import sys
from tqdm import tqdm
from utils import *
from sklearn.metrics import pairwise_distance

if __name__ == '__main__':


	parser = argparse.ArgumentParser(description='Retrieval Evaluation')
	parser.add_argument('--cp-path', type=str, default=None, metavar='Path', help='Path for checkpointing')
	parser.add_argument('--data-path', type=str, default='./data/', metavar='Path', help='Path to data')
	parser.add_argument('--batch-size', type=int, default=64, metavar='N', help='input batch size for training (default: 64)')
	parser.add_argument('--n-workers', type=int, default=4, metavar='N', help='Workers for data loading. Default is 4')
	parser.add_argument('--model', choices=['vgg', 'resnet', 'densenet'], default='resnet')
	parser.add_argument('--dropout-prob', type=float, default=0.25, metavar='p', help='Dropout probability (default: 0.25)')
	parser.add_argument('--k-list', nargs='+', required=True, help='List of k values for R@K computation')
	parser.add_argument('--no-cuda', action='store_true', default=False, help='Disables GPU use')
	args = parser.parse_args()
	args.cuda = True if not args.no_cuda and torch.cuda.is_available() else False
	if type(args.k_list[0]) is str:
		args.k_list = [int(x) for x in args.k_list[0].split(',')]

	print(args)

	transform_test = transforms.Compose([transforms.CenterCrop(224), transforms.ToTensor(), transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])
	validset = datasets.ImageFolder(args.data_path, transform=transform_test)
	valid_loader = torch.utils.data.DataLoader(validset, batch_size=args.batch_size, shuffle=False, num_workers=args.n_workers)

	r_at_k_e2e = {'R@'+str(x):0 for x in args.k_list}
	r_at_k_cos = {'R@'+str(x):0 for x in args.k_list}

	ckpt = torch.load(args.cp_path, map_location = lambda storage, loc: storage)
	try :
		dropout_prob, n_hidden, hidden_size, softmax, n_classes = ckpt['dropout_prob'], ckpt['n_hidden'], ckpt['hidden_size'], ckpt['sm_type'], ckpt['n_classes']
	except KeyError as err:
		print("Key Error: {0}".format(err))
		print('\nProbably old cp has no info regarding classifiers arch!\n')
		n_hidden, hidden_size, softmax, n_classes = get_classifier_config_from_cp(ckpt)
		dropout_prob = args.dropout_prob

	if args.model == 'vgg':
		model = vgg.VGG('VGG19', nh=n_hidden, n_h=hidden_size, dropout_prob=dropout_prob, sm_type=softmax, n_classes=n_classes)
	elif args.model == 'resnet':
		model = resnet.ResNet50(nh=n_hidden, n_h=hidden_size, dropout_prob=dropout_prob, sm_type=softmax, n_classes=n_classes)
	elif args.model == 'densenet':
		model = densenet.DenseNet121(nh=n_hidden, n_h=hidden_size, dropout_prob=dropout_prob, sm_type=softmax, n_classes=n_classes)

	print(model.load_state_dict(ckpt['model_state'], strict=False))

	if args.cuda:
		device = get_freer_gpu()
		model = model.cuda(device)

	e2e_scores = {}
	embeddings = []
	labels = []

	model.eval()

	iterator = tqdm(valid_loader, total=len(valid_loader))

	with torch.no_grad():

		for batch in iterator:

			x, y = batch

			if args.cuda:
				x, y = x.to(device), y.to(device)

			emb = model.forward(x).detach()

			embeddings.append(emb.detach().cpu())
			labels.append(y)

	embeddings = torch.cat(embeddings, 0)
	labels = torch.cat(labels, 0)

	print('\nEmbedding done')

	model = model.cpu()

	def compute_similarity(x, y, *args, **kwargs):
		x, y = torch.from_numpy(x).unsqueeze(0), torch.from_numpy(x).unsqueeze(0)

		x_y = torch.cat([x,y],1)
		y_x = torch.cat([y,x],1)

		d_xy = model.forward_bin(x_y).squeeze().item()
		d_yx = model.forward_bin(y_x).squeeze().item()

		return (d_xy+d_yx)/2.0

	sim_matrix = pairwise_distance(embeddings, metric=compute_similarity, n_jobs=-1)

	with torch.no_grad():

		iterator = tqdm(enumerate(labels), total=len(labels))
		for i, label_1 in iterator:

			enroll_ex = str(i)

			e2e_scores[enroll_ex] = []
			cos_scores[enroll_ex] = []

			for j, label_2 in enumerate(labels):
				
				if i==j: continue ## skip same example

				e2e_scores[enroll_ex].append( [sim_matrix[i][j], label_2] )

	print('\nScoring done')

for i, label in enumerate(labels):
	eval_ex = str(i)
	sorted_e2e_classes = np.array(sorted(e2e_scores[eval_ex], reverse=True))[:,1]

	for k in args.k_list:
		if label in sorted_e2e_classes[:k]:
			r_at_k_e2e['R@'+str(k)]+=1

for k in args.k_list:
	r_at_k_e2e['R@'+str(k)]/=len(labels_list)

print('\nR@k:')
print(r_at_k_e2e)
print('\n')