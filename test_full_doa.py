import os.path
import sys
import glob
import json

import numpy as np
import tensorflow as tf
import random
import math
from scipy.io import wavfile
from matplotlib import pyplot as plt
import matplotlib.ticker as mtick
from functools import reduce

import model
import input_data
import eval


def main(_):
    json_dir = './config.json'
    with open(json_dir) as config_json:
        config = json.load(config_json)

    tf.logging.set_verbosity(tf.logging.INFO)

    # Start a new TensorFlow session.
    sess = tf.InteractiveSession()

    phase_specs = tf.placeholder(tf.float32, shape=[None, config['context_window_width'], 129, 4], name='phase_specs')

    model_settings = model.create_model_settings(
        dim_direction_label=config['dim_direction_label'],
        sample_rate=config["sample_rate"],
        win_len=config['win_len'],
        win_shift=config['win_shift'],
        nDFT=config['nDFT'],
        context_window_width=config['context_window_width'])

    with tf.variable_scope('CNN'):
        predict_logits = model.doa_cnn(phase_specs=phase_specs, model_settings=model_settings, is_training=True)
    CNN_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='CNN')

    print('-' * 80)
    print('CNN vars')
    nparams = 0
    for v in CNN_vars:
        v_shape = v.get_shape().as_list()
        v_n = reduce(lambda x, y: x * y, v_shape)
        nparams += v_n
        print('{} ({}): {}'.format(v.get_shape().as_list(), v_n, v.name))
    print('Total params: {} ({:.2f} MB)'.format(nparams, (float(nparams) * 4) / (1024 * 1024)))
    print('-' * 80)

    tf.global_variables_initializer().run()
    init_local_variable = tf.local_variables_initializer()
    init_local_variable.run()
    if config['start_checkpoint']:
        model.load_variables_from_checkpoint(sess, config['start_checkpoint'], var_list=CNN_vars)

    rir_data_dir = config['rir_data_dir']
    rir_file_list = glob.glob(os.path.join(rir_data_dir, "*.wav"))

    reverb = config['reverb']
    reverb.sort()

    room_index = config['room_idx']
    room_index.sort()

    # find testing files
    testing_file_list = glob.glob(os.path.join(config['testing_data_dir'], "*.wav"))

    if not len(testing_file_list):
        Exception("No wav files found at " + testing_file_list)
    if not len(rir_file_list):
        Exception("No wav files found at " + rir_data_dir)

    for room_idx, room in enumerate(room_index):

        for reverb_idx, reverb_percent in enumerate(reverb):

            reverb_wav = input_data.gen_moving_direct_wav(wav_dir=config['testing_data_dir'],
                                                          rir_dir=config['rir_data_dir'],
                                                          doa_interval=config['direction_range'],
                                                          deg_per_sec=config['deg_per_sec'],
                                                          reverb_percent=reverb_percent,
                                                          room_index=room)

            voiced_idx, voiced_percent = input_data.get_dual_channel_voiced_idx(reverb_wav,
                                                                                win_len=config['win_len'],
                                                                                win_shift=config['win_shift'],
                                                                                nDFT=config['nDFT'],
                                                                                context_window_width=config['context_window_width'],
                                                                                rms_thre=3e-1)

            duration = reverb_wav.shape[1] / 16e3

            testing_specs = input_data.get_reverb_specs(reverb_wav=reverb_wav,
                                                        win_len=config['win_len'],
                                                        win_shift=config['win_shift'],
                                                        nDFT=config['nDFT'],
                                                        context_window_width=config['context_window_width'])

            num_frames = testing_specs.shape[0]
            print(num_frames)

            label, label_argmax = input_data.get_moving_wav_labels(num_frames,
                                                                   config['win_shift'],
                                                                   config['deg_per_sec'],
                                                                   config['direction_range'])

            logits = sess.run(
                predict_logits,
                feed_dict={phase_specs: testing_specs})

            testing_predict = eval.get_deg_from_logits(logits,
                                                       doa_interval=config['direction_range'],
                                                       num_doa_class=config['dim_direction_label'])

            wavfile.write(filename='./moving.wav', data=np.transpose(reverb_wav), rate=16000)

            time_idx = np.arange(0, len(reverb_wav[0, :]), math.floor(len(reverb_wav[0, :]) / 5))
            time_text = time_idx * duration / len(reverb_wav[0, :])
            time_text = [str(round(float(label), 2)) for label in time_text]
            idx = range(len(label_argmax))

            label_idx = np.arange(0, len(label_argmax), math.floor(len(label_argmax)/5))
            label_text = label_idx * duration / len(label_argmax)
            label_text = [str(round(float(label), 2)) for label in label_text]

            plt.figure(figsize=(20, 10))

            plt.subplot(311)
            plt.xlabel('time (s)')
            plt.xticks(time_idx, time_text)
            plt.ylabel('X')
            plt.ylim(-1, 1)
            plt.plot(reverb_wav[0, :])

            ax = plt.gca()
            ax.xaxis.set_label_coords(1.05, -0.025)

            plt.subplot(312)
            plt.xlabel('time (s)')
            plt.xticks(time_idx, time_text)
            plt.ylim(-1, 1)
            plt.ylabel('Y')
            plt.plot(reverb_wav[1, :])

            ax = plt.gca()
            ax.xaxis.set_label_coords(1.05, -0.025)

            # only plot result for voiced part
            testing_predict = testing_predict.astype(float)
            silent_idx = np.logical_not(voiced_idx)
            testing_predict[silent_idx] = np.nan

            label_argmax = label_argmax.astype(float)
            label_argmax[silent_idx] = np.nan

            plt.subplot(313)
            plt.ylim(0, 140)
            plt.ylabel('DOA / degree')
            plt.xlabel('time (s)')
            plt.xticks(label_idx, label_text)
            plt.plot(idx, label_argmax, 'bs', label='ground truth', markersize=2.15)
            plt.plot(idx, testing_predict, 'r.', label='predict', markersize=2)
            plt.legend(loc='upper left')
            plt.grid(True)

            ax = plt.gca()
            ax.xaxis.set_label_coords(1.05, -0.025)

            fig_save_path = os.path.join('./figures', 'v4_voiced',
                                         os.path.basename(config['testing_data_dir']))
            if not os.path.exists(fig_save_path):
                os.makedirs(fig_save_path)
            file_name = 'moving_plot_reverb' + str(reverb_percent) + '_room' + str(room) + '.png'
            save_path = os.path.join(fig_save_path, file_name)
            plt.savefig(save_path)


if __name__ == '__main__':
    tf.app.run(main=main, argv=[sys.argv[0]])
