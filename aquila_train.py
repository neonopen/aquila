"""
Version 2 of Aquila Training.

This borrows more from the Inception training module, since I'm more able to
comprehend it moreso now.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import copy
from datetime import datetime
import os.path
import re
import time

import numpy as np
import tensorflow as tf

from net import aquila_model as aquila
from net.slim import slim
from config import *

BATCH_SIZE *= num_gpus


def _tower_loss(inputs, labels, conf, scope, is_test=False):
    """
    Calculates the loss for a single tower, which is specified by scope.

    NOTES:
        Unlike in the original implementation for Inception, we will instead
        be dequeueing multiple batches for each tower.

    :param inputs: A BATCH_SIZE x 299 x 299 x 3 sized float32 tensor (images)
    :param labels: A [BATCH_SIZE x BATCH_SIZE x DEMOGRAPHIC_GROUPS] label
    matrix.
    :param conf: A [BATCH_SIZE x BATCH_SIZE x DEMOGRAPHIC_GROUPS] confidence
    matrix.
    :param scope: The tower name (i.e., tower_0)
    :returns: The total loss op.
    """

    # construct an instance of Aquila
    logits = aquila.inference(inputs, abs_feats, for_training=True,
                              restore_logits=restore_logits, scope=scope,
                              regularization_strength=WEIGHT_DECAY)
    # create the loss graph
    aquila.loss(logits, labels, conf)

    # create the accuracy graph
    accuracy = aquila.accuracy(logits, labels)
    if is_test:
        return accuracy
    # accuracy_averages = tf.train.ExponentialMovingAverage(0.9,
    # name='accuracy')
    # accuracy_averages_op = accuracy_averages.apply([accuracy])

    # fetch the actual losses, both the ranknet and the regularization loss
    # functions.
    losses = tf.get_collection(slim.losses.LOSSES_COLLECTION, scope)
    regularization_losses = tf.get_collection(
                                tf.GraphKeys.REGULARIZATION_LOSSES)
    total_loss = tf.add_n(losses + regularization_losses, name='total_loss')
    loss_averages = tf.train.ExponentialMovingAverage(0.995, name='avg')
    loss_averages_op = loss_averages.apply(losses + [total_loss, accuracy])
    for l in losses + [total_loss]:
        loss_name = re.sub('%s_[0-9]*/' % aquila.TOWER_NAME, '', l.op.name)
        tf.scalar_summary(loss_name +' (raw)', l)
        tf.scalar_summary(loss_name, loss_averages.average(l))
    tf.scalar_summary('accuracy/raw', accuracy)
    tf.scalar_summary('accuracy/smoothed', loss_averages.average(accuracy))
    with tf.control_dependencies([loss_averages_op]):
        total_loss = tf.identity(total_loss)
    return total_loss, accuracy


def _average_gradients(tower_grads):
    """
    Calculate the average gradient for each shared variable across all towers.

    NOTES:
        This function provides a synchronization point across all towers.

    :param tower_grads: List of lists of (gradient, variable) tuples. The outer
    list is over individual gradients. The inner list is over the gradient
    calculation for each tower.
    :returns: List of pairs of (gradient, variable) where the gradient has been
    averaged across all towers.
    """
    average_grads = []
    for grad_and_vars in zip(*tower_grads):
        # Note that each grad_and_vars looks like the following:
        #   ((grad0_gpu0, var0_gpu0), ... , (grad0_gpuN, var0_gpuN))
        grads = []
        for g, _ in grad_and_vars:
            # Add 0 dimension to the gradients to represent the tower.
            expanded_g = tf.expand_dims(g, 0)

            # Append on a 'tower' dimension which we will average over below.
            grads.append(expanded_g)

        # Average over the 'tower' dimension.
        grad = tf.concat(0, grads)
        grad = tf.reduce_mean(grad, 0)

        # Keep in mind that the Variables are redundant because they are shared
        # across towers. So .. we will just return the first tower's pointer to
        # the Variable.
        v = grad_and_vars[0][1]
        grad_and_var = (grad, v)
        average_grads.append(grad_and_var)
    return average_grads


def train(inp_mgr, test_mgr, ex_per_epoch):
    """
    Trains the network for some number of epochs.

    :param inp_mgr: An instance of the input manager.
    :param test_mgr: An instance of the input manager but for testing /
    validation
    :param num_epochs: The number of epochs to run for.
    :param ex_per_epoch: The number of examples per epoch.
    """
    global_step = tf.get_variable(
                'global_step', [],
                initializer=tf.constant_initializer(0), trainable=False)

    num_batches_per_epoch = ex_per_epoch / BATCH_SIZE
    max_steps = int(num_batches_per_epoch * NUM_EPOCHS)
    decay_steps = 10000 # int(num_batches_per_epoch * num_epochs_per_decay)
    lr = tf.train.exponential_decay(initial_learning_rate,
                                    global_step,
                                    decay_steps,
                                    learning_rate_decay_factor,
                                    staircase=True)
    # Create an optimizer that performs gradient descent.
    opt = tf.train.RMSPropOptimizer(lr, RMSPROP_DECAY,
                                    momentum=RMSPROP_MOMENTUM,
                                    epsilon=RMSPROP_EPSILON)

    # Get images and labels for ImageNet and split the batch across GPUs.
    assert BATCH_SIZE % num_gpus == 0, (
            'Batch size must be divisible by number of GPUs')
    split_batch_size = int(BATCH_SIZE / num_gpus)

    input_summaries = copy.copy(tf.get_collection(tf.GraphKeys.SUMMARIES))

    # Calculate the gradients for each model tower.
    tower_grads = []
    tow_loss_ops = []
    tow_acc_ops = []
    test_tow_acc_ops = []
    for i in xrange(num_gpus):
        with tf.device('/gpu:%d' % i):
            with tf.name_scope('%s_%d' % (aquila.TOWER_NAME, i)) as scope:
                # Calculate the loss for one tower of the ImageNet model. This
                # function constructs the entire ImageNet model but shares the
                # variables across all towers.
                inputs, labels, conf, filenames = inp_mgr.tf_queue.dequeue_many(
                        split_batch_size)
                vinputs, vlabels, vconf, vfilenames = \
                    test_mgr.tf_queue.dequeue_many(split_batch_size)
                tf.scalar_summary('input_queue_size', inp_mgr.tf_queue.size())
                tf.image_summary('images', inputs, max_images=4,
                                 collections=[tf.GraphKeys.SUMMARIES],
                                 name=None)
                with tf.variable_scope('testtrain') as varscope:

                    loss, accuracy = _tower_loss(inputs, labels, conf, scope)
                    # FOR TESTING
                    varscope.reuse_variables()
                    test_accuracy = _tower_loss(vinputs, vlabels, vconf, scope,
                                                is_test=True)
                    # /FOR TESTING
                # FOR TESTING
                test_tow_acc_ops.append(test_accuracy)
                # /FOR TESTING

                tow_loss_ops.append(loss)
                tow_acc_ops.append(accuracy)
                # Reuse variables for the next tower.
                tf.get_variable_scope().reuse_variables()

                # Retain the summaries from the final tower.
                summaries = tf.get_collection(tf.GraphKeys.SUMMARIES, scope)

                # Retain the Batch Normalization updates operations only from the
                # final tower. Ideally, we should grab the updates from all towers
                # but these stats accumulate extremely fast so we can ignore the
                # other stats from the other towers without significant detriment.
                batchnorm_updates = tf.get_collection(
                        slim.ops.UPDATE_OPS_COLLECTION, scope)

                # Calculate the gradients for the batch of data on this ImageNet
                # tower.
                grads = opt.compute_gradients(loss)

                # Keep track of the gradients across all towers.
                tower_grads.append(grads)
    avg_loss_op = tf.reduce_mean(tf.pack(tow_loss_ops))
    avg_acc_op = tf.reduce_mean(tf.pack(tow_acc_ops))
    test_avg_acc_op = tf.reduce_mean(tf.pack(test_tow_acc_ops))


    test_acc_avg = tf.train.ExponentialMovingAverage(0.95, name='avg_test_acc')
    test_acc_avg_op = test_acc_avg.apply([test_avg_acc_op])
    summaries.append(tf.scalar_summary('validation/accuracy_smoothed', test_acc_avg.average(test_avg_acc_op)))
    with tf.control_dependencies([test_acc_avg_op]):
        test_avg_acc_op = tf.identity(test_avg_acc_op)
    # We must calculate the mean of each gradient. Note that this is the
    # synchronization point across all towers.
    grads = _average_gradients(tower_grads)

    # Add a summaries for the input processing and global_step.
    summaries.extend(input_summaries)

    # Add a summary to track the learning rate.
    summaries.append(tf.scalar_summary('learning_rate', lr))
    summaries.append(tf.scalar_summary('validation/accuracy', test_avg_acc_op))
    # Add histograms for gradients.
    for grad, var in grads:
        if grad is not None:
            summaries.append(
                    tf.histogram_summary(var.op.name + '/gradients', grad))

    # Apply the gradients to adjust the shared variables.
    apply_gradient_op = opt.apply_gradients(grads, global_step=global_step)

    # Add histograms for trainable variables.
    for var in tf.trainable_variables():
        summaries.append(tf.histogram_summary(var.op.name, var))

    # Track the moving averages of all trainable variables.
    # Note that we maintain a "double-average" of the BatchNormalization
    # global statistics. This is more complicated then need be but we employ
    # this for backward-compatibility with our previous models.
    variable_averages = tf.train.ExponentialMovingAverage(
            aquila.MOVING_AVERAGE_DECAY, global_step)

    # Another possibility is to use tf.slim.get_variables().
    variables_to_average = (tf.trainable_variables() +
                            tf.moving_average_variables())
    variables_averages_op = variable_averages.apply(variables_to_average)

    # Group all updates to into a single train op.
    batchnorm_updates_op = tf.group(*batchnorm_updates)
    train_op = tf.group(apply_gradient_op, variables_averages_op,
                                            batchnorm_updates_op)

    # Create a saver.
    saver = tf.train.Saver(tf.all_variables(), max_to_keep=20)

    # Build the summary operation from the last tower summaries.
    summary_op = tf.merge_summary(summaries)

    # Build an initialization operation to run below.
    init = tf.initialize_all_variables()

    # Start running operations on the Graph. allow_soft_placement must be set to
    # True to build towers on GPU, as some of the ops do not have GPU
    # implementations.
    sess = tf.Session(config=tf.ConfigProto(
            allow_soft_placement=True,
            log_device_placement=log_device_placement))
    sess.run(init)

    # restore from a pretrained model (if requested)
    if pretrained_model_checkpoint_path:
        assert tf.gfile.Exists(pretrained_model_checkpoint_path)
        variables_to_restore = tf.get_collection(
                slim.variables.VARIABLES_TO_RESTORE)
        restorer = tf.train.Saver(variables_to_restore)
        restorer.restore(sess, pretrained_model_checkpoint_path)
        print('%s: Pre-trained model restored from %s' %
                    (datetime.now(), pretrained_model_checkpoint_path))

    # start the input managers
    inp_mgr.start(sess)
    test_mgr.start(sess)

    # summary_writer = tf.train.SummaryWriter(
    #             train_dir, graph_def=sess.graph.as_graph_def(add_shapes=True))
    summary_writer = tf.train.SummaryWriter(train_dir, sess.graph_def)
    print('%s: Model running for %i iterations' %
          (datetime.now(), max_steps))
    for step in xrange(0, max_steps):
        start_time = time.time()
        _, avg_loss, avg_acc, lr_float = sess.run([train_op, avg_loss_op,
                                                   avg_acc_op, lr])
        duration = time.time() - start_time
        if inp_mgr.should_stop():
            print('Input manager is requesting a stop')
            break
        if np.isnan(avg_loss):
            print('Model is diverging (omg!) dumping data')
            # summary_str = sess.run(summary_op)
            # summary_writer.add_summary(summary_str, step)
            # checkpoint_path = os.path.join(train_dir, 'model.ckpt')
            # saver.save(sess, checkpoint_path, global_step=step)
            raise Exception('Model diverged with loss = NaN on step %i' % step)
        if step % 1 == 0:
            examples_per_sec = BATCH_SIZE / float(duration)
            format_str = ('%s: step %d, loss = %.4f, accuracy = %.2f (%.1f '
                          'examples/sec; '
                          '%.3f sec/batch) (lr is %g)')
            print(format_str % (datetime.now(), step, avg_loss, avg_acc,
                                examples_per_sec, duration, lr_float))

        if step > 0 and (step % 100 == 0):
            summary_str = sess.run(summary_op)
            summary_writer.add_summary(summary_str, step)

        if step % 100 == 0:
            taao = sess.run(test_avg_acc_op)
            format_str = '%s: Test iteration: %.2f acc'
            print(format_str % (datetime.now(), taao))

        # Save the model checkpoint periodically.
        if step > 0 and (step % 10000 == 0 or (step + 1) == max_steps):
            checkpoint_path = os.path.join(train_dir, 'model.ckpt')
            saver.save(sess, checkpoint_path, global_step=step)
    inp_mgr.stop()
    test_mgr.stop()
