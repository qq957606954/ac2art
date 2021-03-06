# coding: utf-8
#
# Copyright 2018 Paul Andrey
#
# This file is part of ac2art.
#
# ac2art is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ac2art is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ac2art.  If not, see <http://www.gnu.org/licenses/>.

"""Wrapper to build corpus-specific data normalization functions."""

import os
import shutil

import numpy as np

from ac2art.corpora.prototype.utils import _get_normfile_path
from ac2art.utils import import_from_string, CONSTANTS


def build_normalization_functions(corpus):
    """Define and return corpus-specific data normalization functions.

    Return two functions, in the following order:
      - compute_moments
      - normalize_files
    """
    # Gather dataset-specific dependencies.
    main_folder = CONSTANTS['%s_processed_folder' % corpus]
    get_utterances_list, speakers = import_from_string(
        'ac2art.corpora.%s.raw._loaders' % corpus,
        ['get_utterances_list', 'SPEAKERS']
    )

    # Wrap the normalization parameters computing function.
    def compute_moments(file_type, by_speaker=False, store=True):
        """Compute files moments."""
        nonlocal speakers
        # Optionally compute speaker-wise normalization parameters.
        if by_speaker:
            return {
                speaker: _compute_moments(
                    file_type, speaker, store, main_folder, get_utterances_list
                )
                for speaker in speakers
            }
        # Otherwise, compute corpus-wide parameters.
        return _compute_moments(
            file_type, None, store, main_folder, get_utterances_list
        )

    # Wrap the files normalization functon.
    def normalize_files(file_type, norm_type, scope='corpus'):
        """Normalize pre-extracted {0} data of a given type.

        Normalization includes de-meaning and division by either
        standard-deviation or the difference between the extremum
        points (distribution spread). Those parameters may either
        be computed file-wise, speaker-wise or corpus-wide.

        file_type  : one of {{'ema', 'energy', 'lpc', 'lsf', 'mfcc'}}
        norm_type  : normalization divisor to use ('spread' or 'stds')
        scope      : scope of the normalization parameters to use
                     ('corpus' for corpus-wide (default), 'speaker'
                     for speaker-wise and 'file' for file-wise)

        Normalized utterances are stored as .npy files in a
        properly-named folder.
        """
        nonlocal compute_moments, get_utterances_list, main_folder, speakers
        if scope == 'corpus':
            _corpus_wide_normalize(
                file_type, norm_type, None, main_folder,
                get_utterances_list, compute_moments
            )
        elif scope == 'speaker':
            for speaker in speakers:
                _corpus_wide_normalize(
                    file_type, norm_type, speaker, main_folder,
                    get_utterances_list, compute_moments
                )
        elif scope == 'file':
            _file_wise_normalize(
                file_type, norm_type, main_folder, get_utterances_list
            )
        else:
            raise ValueError(
                "'scope' should be one of {'corpus', 'speaker', 'file'}."
            )

    # Adjust the functions' docstrings and return them.
    compute_moments.__doc__ = _compute_moments.__doc__.format(corpus)
    normalize_files.__doc__ = normalize_files.__doc__.format(corpus)
    return compute_moments, normalize_files


def _compute_moments(
        file_type, speaker, store, main_folder, get_utterances_list
    ):
    """Compute file-wise and global mean, deviation and spread of a dataset.

    The dataset must have been produced through extracting operations
    on the initial .ema and .wav files of the {0} dataset.

    file_type  : one of {{'ema', 'energy', 'lsf', 'lpc', 'mfcc'}}
    by_speaker : whether to compute speaker-wise normalization parameters
                 instead of corpus-wide ones (bool, default False)
    store      : whether to store the computed values (bool, default True)

    Return a dict containing the computed values.
    Optionally write it to a dedicated .npy file.
    """
    folder = os.path.join(main_folder, file_type)
    # Compute file-wise means and standard deviations.
    dataset = np.array([
        np.load(os.path.join(folder, name + '_%s.npy' % file_type))
        for name in get_utterances_list(speaker)
    ])
    moments = {
        'file_means': np.array([data.mean(axis=0) for data in dataset]),
        'file_stds': np.array([data.std(axis=0) for data in dataset]),
        'file_spread': np.array([
            data.max(axis=0) - data.min(axis=0) for data in dataset
        ])
    }
    # Compute corpus-wide means, standard deviations and spread.
    dataset = np.concatenate(dataset)
    moments.update({
        'global_means': dataset.mean(axis=0),
        'global_stds': dataset.std(axis=0),
        'global_spread': dataset.max(axis=0) - dataset.min(axis=0)
    })
    # Optionally store the computed values to disk.
    if store:
        path = _get_normfile_path(main_folder, file_type, speaker)
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        np.save(path, moments)
    # Return the dict of computed values.
    return moments


def _conduct_normalization(
        file_type, norm_name, normalize, speaker,
        main_folder, get_utterances_list
    ):
    """Conduct normalization of utterances using a pre-built function."""
    # Establish output folder to use. Build it if needed.
    output_folder = os.path.join(main_folder, file_type + '_norm_' + norm_name)
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)
    # Establish which files to work on.
    input_folder = os.path.join(main_folder, file_type)
    files = [
        name + '_%s.npy' % file_type for name in get_utterances_list(speaker)
    ]
    # Iteratively normalize the files.
    for filename in files:
        data = np.load(os.path.join(input_folder, filename))
        data = normalize(data)
        np.save(os.path.join(output_folder, filename), data)
    # When normalizing articulatory features, copy articulators list.
    if file_type == 'ema':
        shutil.copyfile(
            os.path.join(input_folder, 'articulators'),
            os.path.join(output_folder, 'articulators')
        )


def _corpus_wide_normalize(
        file_type, norm_type, speaker, main_folder,
        get_utterances_list, compute_moments
    ):
    """Normalize a corpus using corpus-wide or speaker-wise parameters."""
    # Gather files' moments. Compute them if needed.
    path = _get_normfile_path(main_folder, file_type, speaker)
    if os.path.isfile(path):
        moments = np.load(path).tolist()
    elif speaker is None:
        moments = compute_moments(file_type, by_speaker=False)
    else:
        moments = compute_moments(file_type, by_speaker=True)[speaker]
    means = moments['global_means']
    norm = moments['global_%s' % norm_type]
    # Iteratively normalize the utterances.
    normalize = lambda utterance: (utterance - means) / norm
    norm_name = norm_type + ('' if speaker is None else '_byspeaker')
    _conduct_normalization(
        file_type, norm_name, normalize, speaker,
        main_folder, get_utterances_list
    )


def _file_wise_normalize(
        file_type, norm_type, main_folder, get_utterances_list
    ):
    """Normalize a corpus using file-specific parameters."""
    # Define a file-wise normalization function.
    if norm_type == 'stds':
        get_norm = lambda utt: utt.std(axis=0)
    elif norm_type == 'spread':
        get_norm = lambda utt: (utt.max(axis=0) - utt.min(axis=0))
    else:
        raise KeyError("'norm_type' should be one of {'stds', 'spread'}.")
    normalize = lambda utt: (utt - utt.mean(axis=0)) / get_norm(utt)
    # Conduct normalization using the previously defined function.
    norm_name = norm_type + '_byfile'
    _conduct_normalization(
        file_type, norm_name, normalize, None, main_folder, get_utterances_list
    )
