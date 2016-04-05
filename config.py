"""
Configuration for Aquila
"""

# where to write event logs and checkpoints
train_dir = '/data/aquila_snaps'

# 'train' or 'validation'
subset = 'train'

# how many gpus to use
num_gpus = 4

# Whether to log device placement.
log_device_placement = False  # this produces *so much* output!

# the number of preprocessing threads to create -- just 2 is more than
# sufficient, even for 4 gpus (apparently?)
num_preprocess_threads = 2

# the number of abstract features to learn
abs_feats = 1024

# ---------------------------------------------------------------------------- #
# Flags governing the type of training.
# ---------------------------------------------------------------------------- #
# Whether or not to restore the logits.
restore_logits = False

# restore the pretrained model from this location
pretrained_model_checkpoint_path = '/data/pretrained/model.ckpt-157585'

# the initial learning rate
initial_learning_rate = 0.05

# epochs after which learning rate decays
num_epochs_per_decay = 5.0

# the learning rate decay factor
learning_rate_decay_factor = 0.16


BATCH_SIZE = 16

NUM_EPOCHS = 50

# Constants dictating the learning rate schedule.
RMSPROP_DECAY = 0.9                # Decay term for RMSProp.
RMSPROP_MOMENTUM = 0.9             # Momentum in RMSProp.
RMSPROP_EPSILON = 1.0              # Epsilon term for RMSProp.

# regularization strength
WEIGHT_DECAY = 0.00004