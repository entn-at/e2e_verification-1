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
from sklearn.metrics.cluster import normalized_mutual_info_score
from sklearn.cluster import KMeans

if __name__ == '__main__':


	parser = argparse.ArgumentParser(description='Clustering Evaluation')
	parser.add_argument('--cp-path', type=str, default=None, metavar='Path', help='Path for checkpointing')
	parser.add_argument('--data-path', type=str, default='./data/', metavar='Path', help='Path to data')
	parser.add_argument('--out-path', type=str, default=None, metavar='Path', help='Path to output embeddings.')
	parser.add_argument('--emb-path', type=str, default=None, metavar='Path', help='Path to precomputed embedding.')
	parser.add_argument('--batch-size', type=int, default=64, metavar='N', help='input batch size for training (default: 64)')
	parser.add_argument('--n-workers', type=int, default=4, metavar='N', help='Workers for data loading. Default is 4')
	parser.add_argument('--model', choices=['vgg', 'resnet', 'densenet'], default='resnet')
	parser.add_argument('--dropout-prob', type=float, default=0.25, metavar='p', help='Dropout probability (default: 0.25)')
	parser.add_argument('--stats', choices=['cars', 'cub', 'sop', 'imagenet'], default='imagenet')
	parser.add_argument('--no-cuda', action='store_true', default=False, help='Disables GPU use')
	args = parser.parse_args()
	args.cuda = True if not args.no_cuda and torch.cuda.is_available() else False

	print(args)

	if args.stats=='cars':
		mean, std = [0.4461, 0.4329, 0.4345], [0.2888, 0.2873, 0.2946]
	elif args.stats=='cub':
		mean, std = [0.4782, 0.4925, 0.4418], [0.2330, 0.2296, 0.2647]
	elif args.stats=='sop':
		mean, std = [0.5603, 0.5155, 0.4796], [0.2939, 0.2991, 0.3085]
	elif args.stats=='imagenet':
		mean, std = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]

	transform_test = transforms.Compose([transforms.Resize(256), transforms.CenterCrop(224), transforms.ToTensor(), transforms.Normalize(mean=mean, std=std)])
	validset = datasets.ImageFolder(args.data_path, transform=transform_test)
	valid_loader = torch.utils.data.DataLoader(validset, batch_size=args.batch_size, shuffle=False, num_workers=args.n_workers)

	n_test_classes = len(validset.classes)
	pred_list = []

	ckpt = torch.load(args.cp_path, map_location = lambda storage, loc: storage)
	try :
		dropout_prob, n_hidden, hidden_size, softmax, n_classes, emb_size = ckpt['dropout_prob'], ckpt['n_hidden'], ckpt['hidden_size'], ckpt['sm_type'], ckpt['n_classes'], ckpt['emb_size']
	except KeyError as err:
		print("Key Error: {0}".format(err))
		print('\nProbably old cp has no info regarding classifiers arch!\n')
		n_hidden, hidden_size, softmax, n_classes = get_classifier_config_from_cp(ckpt)
		dropout_prob = args.dropout_prob
		emb_size = 350

	if args.model == 'vgg':
		model = vgg.VGG('VGG19', nh=n_hidden, n_h=hidden_size, dropout_prob=dropout_prob, sm_type=softmax, n_classes=n_classes, emb_size=emb_size)
	elif args.model == 'resnet':
		model = resnet.ResNet50(nh=n_hidden, n_h=hidden_size, dropout_prob=dropout_prob, sm_type=softmax, n_classes=n_classes, emb_size=emb_size)
	elif args.model == 'densenet':
		model = densenet.DenseNet121(nh=n_hidden, n_h=hidden_size, dropout_prob=dropout_prob, sm_type=softmax, n_classes=n_classes, emb_size=emb_size)

	print(model.load_state_dict(ckpt['model_state'], strict=False))

	if args.cuda:
		device = get_freer_gpu()
		model = model.cuda(device)

	model.eval()

	if args.emb_path:

		emb_labels = torch.load(args.emb_path)
		embeddings, labels = emb_labels['embeddings'], emb_labels['labels']
		del emb_labels
		emb_labels = None

		print('\nEmbeddings loaded')

	else:

		embeddings = []
		labels = []

		iterator = tqdm(valid_loader, total=len(valid_loader))

		with torch.no_grad():

			for batch in iterator:

				x, y = batch

				if args.cuda:
					x = x.to(device)

				emb = model.forward(x)[0].detach()

				embeddings.append(emb.detach().cpu())
				labels.append(y)

		embeddings = torch.cat(embeddings, 0)
		labels = list(torch.cat(labels, 0).squeeze().numpy())

		if args.out_path:
			torch.save({'embeddings':embeddings, 'labels':labels}, args.out_path)

		print('\nEmbedding done')

	kmeans = KMeans(n_clusters=n_test_classes).fit(embeddings)
	print('\n NMI: {}'.format(normalized_mutual_info_score(kmeans.labels_, labels)))