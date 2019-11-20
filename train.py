'''
Copyright (c) 2019 Uber Technologies, Inc.

Licensed under the Uber Non-Commercial License (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at the root directory of this project.

See the License for the specific language governing permissions and
limitations under the License.
'''

#Original LCA imports:

from __future__ import print_function
from __future__ import division

from ast import literal_eval
import tensorflow as tf
import numpy as np
import time
import h5py
import argparse
import os

import network_builders
from tf_plus import BatchNormalization, Lambda, Dropout
from tf_plus import Conv2D, MaxPooling2D, Flatten, Dense, he_normal, relu, Activation
from tf_plus import Layers, SequentialNetwork, l2reg, PreprocessingLayers
from tf_plus import learning_phase, batchnorm_learning_phase
from tf_nets.losses import add_classification_losses
from brook.tfutil import hist_summaries_train, get_collection_intersection, get_collection_intersection_summary, log_scalars, sess_run_dict
from brook.tfutil import summarize_weights, summarize_opt, tf_assert_all_init, tf_get_uninitialized_variables, add_grad_summaries

#Added imports from Danny script:

import pandas as pd
import matplotlib.pyplot as plt
import os
import cv2
import PIL
import psutil
from sklearn.model_selection import train_test_split
from tensorflow import set_random_seed
from tqdm import tqdm
from math import ceil
import math
import sys

#Import keras modules:
import keras
from keras.preprocessing.image import ImageDataGenerator
from keras.preprocessing.image import load_img
from keras.preprocessing.image import array_to_img
from keras.preprocessing.image import img_to_array
from keras.applications.resnet50 import ResNet50
from keras.applications.resnet50 import preprocess_input
from keras.models import Model
from keras.models import Sequential
from keras.layers.convolutional import Conv2D
from keras.layers.convolutional import MaxPooling2D
from keras.layers.pooling import GlobalAveragePooling2D
from keras.layers import Input
from keras.layers.core import Dropout
from keras.layers.core import Flatten
from keras.layers.core import Dense
from keras.callbacks import ModelCheckpoint
from keras.callbacks import ReduceLROnPlateau
from keras.callbacks import EarlyStopping
from keras.activations import softmax
from keras.activations import elu
from keras.activations import relu
from keras.optimizers import Adam
from keras.optimizers import RMSprop
from keras.optimizers import SGD
from keras.layers.normalization import BatchNormalization


def make_parser():
    parser = argparse.ArgumentParser()
    # inputs
    parser.add_argument('--data_dir', type=str, required=True)
    parser.add_argument('--input_dim', type=tuple, default= (299,299,3))
    parser.add_argument('--class_label_count', type=int)
    # model architecture:
    parser.add_argument('--arch', type=str, default='basic', choices=('basic','fc',
    'fc_cust','lenet', 'allcnn','resnet', 'vgg'), help='network architecture')
    parser.add_argument('--num_layers', type=int, default=3)

    # training params
    parser.add_argument('--opt', type=str, default='sgd', choices=('sgd', 'rmsprop', 'adam'))
    parser.add_argument('--lr', type=float, default=.01, help='suggested: .01 sgd, .001 rmsprop, .0001 adam')
    parser.add_argument('--decay_schedule', type=str, default='-1', help='comma separated decay learning rate. allcnn: 200,250,300')
    parser.add_argument('--mom', type=float, default=.9, help='momentum (only has effect for sgd/rmsprop)')
    parser.add_argument('--l2', type=float, default=0)
    parser.add_argument('--num_epochs', type=int, default=1, help='number of epochs')
    parser.add_argument('--train_batch_size', type=int, default=12)
    parser.add_argument('--large_batch_size', type=int, default=240, help='APTOS retinal images: 240')
    parser.add_argument('--test_batch_size', type=int, default=0) # do 0 for all
    parser.add_argument('--no_shuffle', action='store_true')
    parser.add_argument('--shuffle_seed', type=int, default=-1, help='seed if you want to shuffle batches')
    parser.add_argument('--tf_seed', type=int, default=-1, help='tensorflow random seed')

    # eval and outputs
    parser.add_argument('--print_every', type=int, default=100, help='print status update every n iterations')
    parser.add_argument('--output_dir', type=str, default=os.environ.get('GIT_RESULTS_MANAGER_DIR', None), help='output directory')
    parser.add_argument('--eval_every', type=int, default=20, help='eval on entire set')
    parser.add_argument('--log_every', type=int, default=5, help='save tb batch acc/loss every n iterations')
    parser.add_argument('--save_weights', action='store_true', help='save weights to file')
    parser.add_argument('--save_training_grads', action='store_true', help='save mini-batch gradients to file')
    parser.add_argument('--save_every', type=int, default=1, help='save gradients every n iterations (averaged)') # deprecated

    return parser

################# model setup, after architecture is already created

def init_model(model, args):
    img_size = tuple([None] + [dim for dim in args.input_dim])
    input_images = tf.placeholder(dtype='float32', shape=img_size)
    input_labels = tf.placeholder(dtype='int64', shape= [None, args.class_label_count])
    model.a("input_images", input_images)
    model.a("input_labels", input_labels)
    model.a('logits', model(input_images)) # logits is y_pred

def define_training(model, args):
    # define optimizer
    input_lr = tf.placeholder(tf.float32, shape=[]) # placeholder for dynamic learning rate
    model.a('input_lr', input_lr)
    if args.opt == 'sgd':
        optimizer = tf.train.MomentumOptimizer(input_lr, args.mom)
    elif args.opt == 'rmsprop':
        optimizer = tf.train.RMSPropOptimizer(input_lr, momentum=args.mom)
    elif args.opt == 'adam':
        optimizer = tf.train.AdamOptimizer(input_lr)
    model.a('optimizer', optimizer)

    # This adds prob, cross_ent, loss_cross_ent, class_prediction,
    # prediction_correct, accuracy, loss, (loss_reg) in tf_nets/losses.py
    add_classification_losses(model, model.input_labels)

    model.a('train_step', optimizer.minimize(model.loss, var_list=model.trainable_weights))

    # gradients (may be used for mini-batches)
    grads_and_vars = optimizer.compute_gradients(model.loss, model.trainable_weights)
    model.a('grads_to_compute', [grad for grad, _ in grads_and_vars])

    print('All model weights:')
    summarize_weights(model.trainable_weights)
    print('trainable:')
    for x in model.trainable_weights:
        print(x)
    print('non trainable:')
    for x in model.non_trainable_weights:
        print(x)
    print('saved to weights file:')
    for x in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES):
        print(x)
    print('grad summaries:')
    add_grad_summaries(grads_and_vars)
    print('opt summary:')
    summarize_opt(optimizer)

################# methods used for freezing layers

# returns list of variables as np arrays in their original shape
def split_and_shape(one_time_slice, shapes):
    variables = []
    offset = 0
    for shape in shapes:
        num_params = np.prod(shape)
        variables.append(one_time_slice[offset : offset + num_params].reshape(shape))
        offset += num_params
    return variables

################# util for training/eval portion

# flatten and concatentate list of tensors into one np vector
def flatten_all(tensors):
    return np.concatenate([tensor.eval().flatten() for tensor in tensors])

# eval on whole train/test set occasionally, for tuning purposes
def eval_on_entire_dataset(sess, model, y_shape, generator, dim_sum,
    batch_size, tb_prefix, tb_writer, iterations):
    grad_sums = np.zeros(dim_sum)
    num_batches = int(y_shape[0] / batch_size)
    total_acc = 0
    total_loss = 0
    total_loss_no_reg = 0 # loss without counting l2 penalty

    for i in range(num_batches):
        # slice indices (should be large)

        fetch_dict = {
                'accuracy': model.accuracy,
                'loss': model.loss,
                'loss_no_reg': model.loss_cross_ent}

        result_dict = sess_run_dict(sess, fetch_dict, feed_dict={
            model.input_images: generator[i][0],
            model.input_labels: generator[i][1],
            learning_phase(): 0,
            batchnorm_learning_phase(): 1}) # do not use nor update moving averages

        total_acc += result_dict['accuracy']
        total_loss += result_dict['loss']
        total_loss_no_reg += result_dict['loss_no_reg']

    acc = total_acc / num_batches
    loss = total_loss / num_batches
    loss_no_reg = total_loss_no_reg / num_batches

    # tensorboard
    if tb_writer:
        summary = tf.Summary()
        summary.value.add(tag='%s_acc' % tb_prefix, simple_value=acc)
        summary.value.add(tag='%s_loss' % tb_prefix, simple_value=loss)
        summary.value.add(tag='%s_loss_no_reg' % tb_prefix, simple_value=loss_no_reg)
        tb_writer.add_summary(summary, iterations)

    return acc, loss_no_reg

#################

def train_and_eval(sess, model, y_train_shape, train_generator, y_test_shape, val_generator,
    tb_writer, dsets, args):
    # constants
    num_batches = int(y_train_shape[0] / args.train_batch_size)
    print('Training batch size {}, number of iterations: {} per epoch, {} total'.format(
        args.train_batch_size, num_batches, args.num_epochs*num_batches))
    dim_sum = sum([tf.size(var).eval() for var in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)])

    # adaptive learning schedule
    curr_lr = args.lr
    decay_epochs = [int(ep) for ep in args.decay_schedule.split(',')]
    if decay_epochs[-1] > 0:
        decay_epochs.append(-1) # end with something small to stop the decay
    decay_count = 0

    # initializations
    tb_summaries = tf.summary.merge(tf.get_collection('tb_train_step'))
    shuffled_indices = np.arange(y_train_shape[0]) # for no shuffling
    iterations = 0
    chunks_written = 0 # for args.save_every batches
    timerstart = time.time()

    for epoch in range(args.num_epochs):
        # print('-' * 100)
        # print('epoch {}  current lr {:.3g}'.format(epoch, curr_lr))
        if not args.no_shuffle:
            shuffled_indices = np.random.permutation(y_train_shape[0]) # for shuffled mini-batches

        if epoch == decay_epochs[decay_count]:
            curr_lr *= 0.1
            decay_count += 1

        for i in range(num_batches):
            # store current weights and gradients
            if args.save_weights and iterations % args.save_every == 0:
                dsets['all_weights'][chunks_written] = flatten_all(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES))
                chunks_written += 1

            # less frequent, larger evals
            if iterations % args.eval_every == 0:
                # eval on entire train set
                cur_train_acc, cur_train_loss = eval_on_entire_dataset(sess, model,
                y_train_shape, train_generator, dim_sum, args.large_batch_size,
                'eval_train', tb_writer, iterations)

                # eval on entire test/val set
                cur_test_acc, cur_test_loss = eval_on_entire_dataset(sess, model,
                y_test_shape, val_generator, dim_sum, y_test_shape[0],
                'eval_test', tb_writer, iterations)

            # print status update
            if iterations % args.print_every == 0:
                print(('{}: train acc = {:.4f}, test acc = {:.4f}, '
                    + 'train loss = {:.4f}, test loss = {:.4f} ({:.2f} s)').format(iterations,
                    cur_train_acc, cur_test_acc, cur_train_loss, cur_test_loss, time.time() - timerstart))

            # Generate batch of training data according to current slice:
            train_x_single_b, train_y_single_b = train_generator[i]

            # training
            fetch_dict = {'train_step': model.train_step}
            fetch_dict.update(model.update_dict())

            if iterations % args.log_every == 0:
                fetch_dict.update({'tb': tb_summaries})
            if args.save_training_grads:
                fetch_dict['gradients'] = model.grads_to_compute

            result_train = sess_run_dict(sess, fetch_dict, feed_dict={
                model.input_images: train_x_single_b,
                model.input_labels: train_y_single_b,
                model.input_lr: curr_lr,
                learning_phase(): 1,
                batchnorm_learning_phase(): 1})

            # log to tensorboard
            if tb_writer and iterations % args.log_every == 0:
                tb_writer.add_summary(result_train['tb'], iterations)

            if args.save_training_grads:
                dsets['training_grads'][iterations] = np.concatenate(
                    [grad.flatten() for grad in result_train['gradients']])

            iterations += 1

    # save final weight values
    if args.save_weights:
        dsets['all_weights'][chunks_written] = flatten_all(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES))

    # save final evals
    if iterations % args.eval_every == 0:
        # on entire train set
        cur_train_acc, cur_train_loss = eval_on_entire_dataset(sess, model, y_train_shape,
        train_generator, dim_sum, args.large_batch_size, 'eval_train', tb_writer, iterations)

        # on entire test/val set
        cur_test_acc, cur_test_loss = eval_on_entire_dataset(sess, model, y_test_shape,
        val_generator, dim_sum, args.train_batch_size, 'eval_test', tb_writer, iterations)

    # print last status update
    print(('{}: train acc = {:.4f}, test acc = {:.4f}, '
        + 'train loss = {:.4f}, test loss = {:.4f} ({:.2f} s)').format(iterations,
        cur_train_acc, cur_test_acc, cur_train_loss, cur_test_loss, time.time() - timerstart))

def main():
    parser = make_parser()
    args = parser.parse_args()

    if args.tf_seed != -1:
        tf.random.set_random_seed(args.tf_seed)

    if not args.no_shuffle and args.shuffle_seed != -1:
        np.random.seed(args.shuffle_seed)

    #Define params for model:
    SEED = args.tf_seed
    BATCH_SIZE = args.train_batch_size
    CHANNEL_SIZE = args.input_dim[2]
    NUM_EPOCHS = args.num_epochs
    IMG_DIM = args.input_dim[0]
    TRAIN_DIR = 'train/'
    TEST_DIR = 'test/'
    CLASSES = {0: "No DR", 1: "Mild", 2: "Moderate", 3: "Severe", 4: "Proliferative DR"}

    df_train = pd.read_csv(os.path.join(args.data_dir, "train.csv"))
    df_test = pd.read_csv(os.path.join(args.data_dir, "test.csv"))

    print("Training set has {} samples".format(df_train.shape[0]))
    print("Testing set has {} samples".format(df_test.shape[0]))

    #Process image directories into exact file name (include .png):
    def append_ext(fn):
        return fn+".png"

    df_train["id_code"]= df_train["id_code"].apply(append_ext)

    # load data into generator:
    # For some reason the generator wants diagnostic labels in string form:
    df_train['diagnosis'] = df_train['diagnosis'].astype(str)

    _validation_split = 0.20

    #x_train_shape = (int(np.round(df_train.shape[0] * (1 - _validation_split))), IMG_DIM, IMG_DIM, CHANNEL_SIZE)
    #x_test_shape = (int(np.round(df_train.shape[0] * _validation_split)), IMG_DIM, IMG_DIM, CHANNEL_SIZE)
    y_train_shape = (int(np.round(df_train.shape[0] * (1 - _validation_split))), None)
    y_test_shape = (int(np.round(df_train.shape[0] * _validation_split)), None)

    train_datagen = ImageDataGenerator(
        rescale= 1. / 255,
        validation_split= _validation_split)

    train_generator = train_datagen.flow_from_dataframe(
        dataframe=df_train,
        directory= args.data_dir + TRAIN_DIR,
        x_col="id_code",
        y_col="diagnosis",
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        target_size=(IMG_DIM, IMG_DIM),
        subset='training',
        seed = SEED
        )

    val_generator = train_datagen.flow_from_dataframe(
        dataframe=df_train,
        directory= args.data_dir + TRAIN_DIR,
        x_col="id_code",
        y_col="diagnosis",
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        target_size=(IMG_DIM, IMG_DIM),
        subset='validation',
        seed = SEED
        )

    # build model
    if args.arch == 'basic':
        model = network_builders.build_basic_model(args)
    elif args.arch == 'fc':
        model = network_builders.build_network_fc(args)
    elif args.arch == 'fc_cust':
        model = network_builders.build_fc_adjustable(args)
    elif args.arch == 'lenet':
        model = network_builders.build_lenet_conv(args)
    elif args.arch == 'allcnn':
        model = network_builders.build_all_cnn(args)
    elif args.arch == 'resnet':
        model = network_builders.build_resnet(args)
    elif args.arch == 'vgg':
        model = network_builders.build_vgg_half(args)
    else:
        raise Error("Unknown architeciture {}".format(args.arch))

    init_model(model, args)
    define_training(model, args)

    sess = tf.InteractiveSession()
    sess.run(tf.global_variables_initializer())

    for collection in ['tb_train_step']: # 'eval_train' and 'eval_test' added manually later
        tf.summary.scalar(collection + '_acc', model.accuracy, collections=[collection])
        tf.summary.scalar(collection + '_loss', model.loss, collections=[collection])

    tb_writer, hf = None, None
    dsets = {}
    if args.output_dir:
        tb_writer = tf.summary.FileWriter(args.output_dir, sess.graph)
        # set up output for gradients/weights
        if args.save_weights:
            dim_sum = sum([tf.size(var).eval() for var in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)])
            total_iters = args.num_epochs * int(y_train_shape[0] / args.train_batch_size)
            total_chunks = int(total_iters / args.save_every)
            hf = h5py.File(args.output_dir + '/weights', 'w-')

            # write metadata
            var_shapes = np.string_(';'.join([str(var.get_shape()) for var in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)]))
            hf.attrs['var_shapes'] = var_shapes
            var_names = np.string_(';'.join([str(var.name) for var in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)]))
            hf.attrs['var_names'] = var_names

            # all individual weights at every iteration, where all_weights[i] = weights before iteration i:
            dsets['all_weights'] = hf.create_dataset('all_weights', (total_chunks + 1, dim_sum), dtype='f8', compression='gzip')
        if args.save_training_grads:
            dsets['training_grads'] = hf.create_dataset('training_grads', (total_chunks, dim_sum), dtype='f8', compression='gzip')

    train_and_eval(sess, model, y_train_shape, train_generator, y_test_shape,
    val_generator, tb_writer, dsets, args)

    if tb_writer:
        tb_writer.close()
    if hf:
        hf.close()

if __name__ == '__main__':
    main()
