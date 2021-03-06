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

"""Wrappers to design corpus-specific functions to run ABXpy tasks."""

import os
import functools

import h5features as h5f
import pandas as pd
import numpy as np

from ac2art.external.abxpy import abxpy_pipeline, abxpy_task
from ac2art.networks import NeuralNetwork
from ac2art.utils import (
    check_positive_int, check_type_validity, import_from_string, CONSTANTS
)


def build_h5features_extractor(corpus):
    """Define and return a function extracting features to h5 files.

    Return a single function:
      - extract_h5_features
    """
    # Load dependency path and functions.
    abx_folder = os.path.join(CONSTANTS['%s_processed_folder' % corpus], 'abx')
    load_acoustic, load_ema, get_utterances = import_from_string(
        'ac2art.corpora.%s.load._load' % corpus,
        ['load_acoustic', 'load_ema', 'get_utterances']
    )
    # Define features extraction functions.

    def _setup_features_loader(
            audio_features, ema_features, inverter, dynamic_ema, articulators
        ):
        """Build a function to load features associated with an utterance.

        See `extract_h5_features` documentation for arguments.
        """
        nonlocal load_acoustic, load_ema
        # Check that provided arguments make sense.
        if audio_features is None and ema_features is None:
            raise RuntimeError('No features were set to be included.')
        if inverter is not None:
            check_type_validity(inverter, (NeuralNetwork, None), 'inverter')
            if audio_features is None:
                raise RuntimeError(
                    'No acoustic features specified to feed the inverter.'
                )
            elif ema_features is not None:
                raise RuntimeError(
                    'Both ema features and an inverter were specified.'
                )
        # Build the acoustic features loading function.
        if audio_features is not None:
            window = (
                0 if inverter is None or inverter.input_shape[-1] % 11 else 5
            )
            load_audio = functools.partial(
                load_acoustic, audio_type=audio_features, context_window=window
            )
            # Optionally build and return an inverter-based features loader.
            if inverter is not None:
                def invert_features(utterance):
                    """Return the features inverted from an utterance."""
                    pred = inverter.predict(load_audio(utterance))
                    return pred
                return invert_features
            if ema_features is None:
                return load_audio
        # Build the articulatory features loading function.
        if ema_features is not None:
            load_articulatory = functools.partial(
                load_ema, norm_type=ema_features, use_dynamic=dynamic_ema,
                articulators=articulators
            )
            if audio_features is None:
                return load_articulatory
        # When appropriate, build a global features loading function.
        def load_features(utterance):
            """Load the features associated with an utterance."""
            return np.concatenate(
                [load_audio(utterance), load_articulatory(utterance)], axis=1
            )
        return load_features

    def extract_h5_features(
            audio_features=None, ema_features=None, inverter=None,
            output_name='%s_features' % corpus, articulators=None,
            dynamic_ema=True, sampling_rate=100
        ):
        """Build an h5 file recording audio features associated with {0} data.

        audio_features : optional name of audio features to use, including
                         normalization indications
        ema_features   : optional name of ema features' normalization to use
                         (use '' for raw data and None for no EMA data)
        inverter       : optional acoustic-articulatory inverter whose
                         predictions to use, based on the audio features
        output_name    : base name of the output file (default '{0}_features')
        articulators   : optional list of articulators to keep among EMA data
        dynamic_ema    : whether to include dynamic articulatory features
                         (bool, default True)
        sampling_rate  : sampling rate of the frames, in Hz (int, default 100)
        """
        # Arguments serve modularity; pylint: disable=too-many-arguments
        nonlocal abx_folder, get_utterances, _setup_features_loader
        # Build the abx folder, if necessary.
        if not os.path.isdir(abx_folder):
            os.makedirs(abx_folder)
        # Check that the destination file does not exist.
        output_file = os.path.join(abx_folder, '%s.features' % output_name)
        if os.path.isfile(output_file):
            raise FileExistsError("File '%s' already exists." % output_file)
        # Set up the features loading function.
        load_features = _setup_features_loader(
            audio_features, ema_features, inverter, dynamic_ema, articulators
        )
        # Load the list of utterances and process them iteratively.
        utterances = get_utterances()
        with h5f.Writer(output_file) as writer:
            for i in range(0, len(utterances), 100):
                # Load or compute utterances list, features and time labels.
                items = utterances[i:i + 100]
                features = [load_features(item) for item in items]
                labels = [
                    np.arange(len(data)) / sampling_rate for data in features
                ]
                # Write the currently processed utterances' data to h5.
                writer.write(
                    h5f.Data(items, labels, features, check=True),
                    groupname='features', append=True
                )

    # Adjust the features extraction function's docstring and return it.
    extract_h5_features.__doc__ = extract_h5_features.__doc__.format(corpus)
    return extract_h5_features


def build_abxpy_callers(corpus):
    """Define and return corpus-specific functions to run ABXpy tasks.

    Return four functions, in that order:
      - abx_from_features
      - make_abx_task
      - make_itemfile
      - load_abx_scores
    """
    # pylint: disable=too-many-statements
    # Load dependency path and function.
    abx_folder = os.path.join(CONSTANTS['%s_processed_folder' % corpus], 'abx')
    get_utterances = import_from_string(
        'ac2art.corpora.%s.load._load' % corpus, 'get_utterances'
    )
    load_phone_labels = import_from_string(
        'ac2art.corpora.%s.raw._loaders' % corpus, 'load_phone_labels'
    )
    # Define the functions.

    def _phones_to_itemfile(utterance, symbols):
        """Build a dict of item file rows for a given utterance."""
        nonlocal load_phone_labels
        phones = load_phone_labels(utterance)
        times = [round(time - phones[0][0], 3) for time, _ in phones[:-1]]
        phones = [symbols[phone] for _, phone in phones]
        return {
            '#file': [utterance] * (len(times) - 1),
            'onset': times[:-1],
            'offset': times[1:],
            '#phone': phones[1:-1],
            'context': [
                phones[i - 1] + '_' + phones[i + 1]
                for i in range(1, len(times))
            ],
            'speaker': utterance.split('_')[0]
        }

    def get_task_name(fileset, limit_phones):
        """Return the base name of an ABX task file based on parameters."""
        nonlocal corpus
        fileset = '' if fileset is None else fileset + '_'
        reduced = 'reduced_' * limit_phones
        return corpus + '_' + fileset + reduced

    def make_itemfile(fileset=None, limit_phones=False):
        """Build a .item file for ABXpy recording {0} phone labels.

        fileset      : optional set name whose utterances to use (str)
        limit_phones : whether to aggregate some phonemes, using
                       the 'common_reduced' column of the symbols
                       file as mapping (bool, default False)
        """
        nonlocal abx_folder, corpus, get_utterances, _phones_to_itemfile
        print('Creating item file...')
        # Establish the item file's location.
        output_file = get_task_name(fileset, limit_phones) + 'phones.item'
        output_file = os.path.join(abx_folder, output_file)
        # Write the item file's header.
        columns = ['#file', 'onset', 'offset', '#phone', 'context', 'speaker']
        with open(output_file, mode='w') as itemfile:
            itemfile.write(' '.join(columns) + '\n')
        # Load the corpus-specific to cross-corpus phone symbols mapping dict.
        # note: non-ipa cross-corpus symbols are used because ABXpy
        #       (python 2) does not support non-ascii characters
        symbols = pd.read_csv(
            CONSTANTS['symbols_file'], index_col=corpus
        )['common' + '_reduced' * limit_phones].to_dict()
        # Iteratively add utterances phone labels to the item file.
        for utterance in get_utterances(fileset):
            items = pd.DataFrame(_phones_to_itemfile(utterance, symbols))
            items[columns].to_csv(
                output_file, index=False, header=False,
                sep=' ', mode='a', encoding='utf-8'
            )
        print('Done creating %s file.' % output_file)

    def make_abx_task(fileset=None, byspeaker=True, limit_phones=False):
        """Build a .abx ABXpy task file associated with {0} phones.

        fileset      : optional set name whose utterances to use (str)
        byspeaker    : whether to discriminate pairs from the same
                       speaker only (bool, default True)
        limit_phones : whether to aggregate some phonemes, using
                       the 'common_reduced' column of the symbols
                       file as mapping (bool, default False)
        """
        nonlocal abx_folder, corpus, make_itemfile
        print('Creating task file...')
        # Build the item file if necessary.
        task_name = get_task_name(fileset, limit_phones)
        item_file = os.path.join(abx_folder, task_name + 'phones.item')
        if not os.path.isfile(item_file):
            make_itemfile(fileset, limit_phones)
        else:
            print('Using found %s file.' % item_file)
        # Establish the task file's path and the ABXpy task's 'on' argument.
        output_file = os.path.join(
            abx_folder, task_name + ('byspk_' * byspeaker) + 'task.abx'
        )
        within = 'context speaker' if byspeaker else 'context'
        # Run the ABXpy task module.
        abxpy_task(item_file, output_file, on='phone', by=within)
        print('Done creating %s file.' % output_file)

    def abx_from_features(
            features, fileset=None, byspeaker=True,
            limit_phones=False, n_jobs=1
        ):
        """Run the ABXpy pipeline on a set of pre-extracted {0} features.

        features     : name of a h5 file of {0} features created with
                       the `extract_h5_features` function (str)
        fileset      : optional name of a fileset whose utterances'
                       features to use (str)
        byspeaker    : whether to discriminate pairs from the same
                       speaker only (bool, default True)
        limit_phones : whether to aggregate some phonemes, using
                       the 'ipa_reduced' column of the {0} symbols
                       file as mapping (bool, default False)
        n_jobs       : number of CPU cores to use (positive int, default 1)
        """
        nonlocal abx_folder, corpus, make_abx_task
        check_type_validity(features, str, 'features')
        check_type_validity(fileset, (str, type(None)), 'fileset')
        check_positive_int(n_jobs, 'n_jobs')
        # Declare the path to the task file.
        task_name = get_task_name(fileset, limit_phones)
        task_name += 'byspk_' * byspeaker
        task_file = os.path.join(abx_folder, task_name + 'task.abx')
        # Declare paths to the input features and output scores files.
        features_file = os.path.join(abx_folder, features + '.features')
        scores_file = features + '_' + task_name.split('_', 1)[1] + 'abx.csv'
        scores_file = os.path.join(abx_folder, scores_file)
        # Check that the features file exists.
        if not os.path.exists(features_file):
            raise FileNotFoundError("No such file: '%s'." % features_file)
        # Build the ABX task file if necessary.
        if not os.path.isfile(task_file):
            make_abx_task(fileset, byspeaker, limit_phones)
        else:
            print('Using found %s file.' % task_file)
        # Run the ABXpy pipeline.
        abxpy_pipeline(features_file, task_file, scores_file, n_jobs)
        # Replace phone symbols with IPA ones in the scores file.
        add_ipa_symbols(scores_file)


    def load_abx_scores(filename):
        """Load, aggregate and return some pre-computed abx scores."""
        nonlocal abx_folder, corpus
        # Load the ABX scores.
        path = os.path.join(abx_folder, filename + '_abx.csv')
        data = pd.read_csv(path)
        # Collapse the scores (i.e. forget about contexts and speakers).
        data['score'] *= data['n']
        data['phones'] = data.apply(
            lambda row: '_'.join(sorted([row['phone_1'], row['phone_2']])),
            axis=1
        )
        scores = data.groupby('phones')[['score', 'n']].sum()
        scores['score'] /= scores['n']
        # Return the properly-formatted scores.
        return scores

    # Adjust functions' docstrings and return them.
    functions = (
        abx_from_features, make_abx_task, make_itemfile, load_abx_scores
    )
    for function in functions:
        function.__doc__ = function.__doc__.format(corpus)
    return functions


def add_ipa_symbols(scores_file):
    """Replace phone symbols in an ABXpy scores file with IPA ones."""
    print('Replacing phoneme symbols with IPA ones...')
    scores = pd.read_csv(scores_file, sep='\t')
    symbols = pd.read_csv(CONSTANTS['symbols_file'], index_col='common')
    symbols = symbols['ipa'].to_dict()
    for col in ('phone_1', 'phone_2'):
        scores[col] = scores[col].apply(symbols.get)
    scores.to_csv(scores_file, sep=',', index=False)
    print('Done updating scores file.')
