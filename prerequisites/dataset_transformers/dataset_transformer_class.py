# -*- coding: utf-8 -*-
"""Transform datasets annotations into a standard desired format."""

import json
import os
import pickle
import shutil
from zipfile import ZipFile

import numpy as np
from PIL import Image


class DatasetTransformer:
    """
    A class to transform annotations of a given dataset.

    Datasets supported:
        - VRD
        - VG200
        - VG80K
        - VGMSDN
        - VGVTE
        - VrR-VG
        - sVG
        - UnRel
    """

    def __init__(self, config):
        """Initialize transformer providing the dataset name."""
        self._dataset = config.dataset
        self._glove_txt = config.glove_txt
        self._orig_annos_path = config.orig_annos_path
        self._orig_images_path = config.orig_img_path
        assert os.path.exists(self._orig_images_path)
        base = config.paths['json_path'] + self._dataset
        # When training the baseline model, please comment out the line below; otherwise, keep it uncommented.
        self._conceptnet_json = base + '_conceptnet.json' 
        self._merged_json = base + '_merged.pkl'
        self._negative_json = base + '_negatives.json'
        self._preddet_json = base + '_preddet.json'
        self._predcls_json = base + '_predcls.json'
        self._predicate_json = base + '_predicates.json'
        self._probability_json = base + '_probabilities.json'
        self._object_json = base + '_objects.json'
        self._word2vec_json = base + '_word2vec.json'
        self.r1_preds = []  # rule 1 predicates
        self.r2_preds = []  # rule 2 predicates

    def transform(self):
        """Run the transformation pipeline."""
        jsons = [
            # When training the baseline model, please comment out the line below; otherwise, keep it uncommented.
            self._conceptnet_json, 
            self._preddet_json,
            self._predicate_json,
            self._object_json,
            self._word2vec_json
        ]
        if not all(os.path.exists(anno) for anno in jsons):
            self.download_annotations()
            annos = self.create_relationship_json()
            annos = self._transform_annotations(annos)
            predicates, objects = self.save_predicates_objects(annos)
            if not os.path.exists(self._word2vec_json):
                self.save_word2vec_vectors(predicates, objects)
            # When training the baseline model, please comment out the line below; otherwise, keep it uncommented.
            if not os.path.exists(self._conceptnet_json):
               # When training the baseline model, please comment out the line below; otherwise, keep it uncommented.
               self.save_conceptnet_vectors(predicates, objects)
            annos = self.update_labels(annos, predicates, objects)
            with open(self._preddet_json, 'w') as fid:
                json.dump(annos, fid)
        if self._dataset not in {'VG80K', 'VrR-VG'}:
            with open(self._preddet_json) as fid:
                annos = json.load(fid)
            if not os.path.exists(self._predcls_json):
                self.create_pred_cls_json(annos, predicates)
            with open(self._predicate_json) as fid:
                predicates = json.load(fid)
            with open(self._object_json) as fid:
                objects = json.load(fid)
            if not os.path.exists(self._probability_json):
                with open(self._predcls_json) as fid:
                    annos = json.load(fid)
                prob_matrix = self.compute_relationship_probabilities(
                    annos, predicates, objects)
                with open(self._probability_json, 'w') as fid:
                    json.dump(prob_matrix, fid)
            # Merged classes
            if not os.path.exists(self._merged_json):
                with open(self._predcls_json) as fid:
                    annos = json.load(fid)
                connections = self.compute_class_merging(annos, predicates)
                annos = self.update_annos_with_syns(annos, connections)
                with open(self._predcls_json, 'w') as fid:
                    json.dump(annos, fid)
                with open(self._preddet_json) as fid:
                    annos = json.load(fid)
                annos = self.update_annos_with_syns(annos, connections)
                with open(self._preddet_json, 'w') as fid:
                    json.dump(annos, fid)
                with open(self._merged_json, 'wb') as fid:
                    pickle.dump(connections, fid)
            # Negatives
            if not os.path.exists(self._negative_json):
                self.save_negative_json()

    @staticmethod
    def create_relationship_json():
        """
        Transform relationship annotations.

        Returns a list of dicts:
        {
            'filename': filename (no path),
            'split_id': int, 0/1/2 for train/val/test,
            'height': int, image height in pixels,
            'width': int, image width in pixels,
            'relationships': [
                {
                    'subject': str, subject_name,
                    'subject_box': [y_min, y_max, x_min, x_max],
                    'predicate': str, predicate_name
                    'object': object_name,
                    'object_box': [y_min, y_max, x_min, x_max]
                }
            ]
        }
        """
        return []

    def create_pred_cls_json(self, annos, predicates):
        """
        Annotate all possible pairs, adding background classes.

        Saves a list of dicts:
        {
            'filename': filename (no path),
            'split_id': int, 0/1/2 for train/val/test,
            'height': int, image height in pixels,
            'width': int, image width in pixels,
            'objects': {
                'ids': list of int,
                'names': list of str,
                'boxes': list of 4-tuples
            },
            'relations': {
                'ids': list of int,
                'names': list of str,
                'subj_ids': list of int,
                'obj_ids': list of int
            }
        }
        """
        for anno in annos:
            pairs = {
                (s, o): []
                for s in range(len(anno['objects']['ids']))
                for o in range(len(anno['objects']['ids']))
                if s != o
            }
            for rel in range(len(anno['relations']['subj_ids'])):
                subj_id = anno['relations']['subj_ids'][rel]
                obj_id = anno['relations']['obj_ids'][rel]
                if (subj_id, obj_id) not in pairs:
                    pairs[(subj_id, obj_id)] = []
                pairs[(subj_id, obj_id)].append((
                    anno['relations']['names'][rel],
                    anno['relations']['ids'][rel]
                ))
            pairs = {
                rel_tuple:
                    rels if any(rels)
                    else [('__background__', len(predicates) - 1)]
                for rel_tuple, rels in pairs.items()}
            pairs = np.array([
                (subj_id, obj_id, name, pred_id)
                for (subj_id, obj_id), preds in pairs.items()
                for (name, pred_id) in preds
            ])
            if pairs.size:
                anno['relations'] = {
                    'subj_ids': pairs[:, 0].astype(int).tolist(),
                    'obj_ids': pairs[:, 1].astype(int).tolist(),
                    'names': pairs[:, 2].astype(str).tolist(),
                    'ids': pairs[:, 3].astype(int).tolist()
                }
        with open(self._predcls_json, 'w') as fid:
            json.dump(annos, fid)

    @staticmethod
    def download_annotations():
        """Download dataset annotations."""
        pass

    def save_predicates_objects(self, annos):
        """Save predicates and objects lists and embeddings."""
        predicates = sorted(list(set(
            name for anno in annos for name in anno['relations']['names']
            if name != '__background__'
        )))
        predicates.append('__background__')
        with open(self._predicate_json, 'w') as fid:
            json.dump(predicates, fid)
        objects = sorted(list(set(
            name for anno in annos for name in anno['objects']['names']
        )))
        with open(self._object_json, 'w') as fid:
            json.dump(objects, fid)
        return predicates, objects

    def save_word2vec_vectors(self, predicates, objects):
        """Build word2vec dictionary of dataset vocabulary."""
        if not os.path.exists(self._glove_txt):
            self._download_glove()
        voc = {word for name in predicates + objects for word in name.split()}
        with open(self._glove_txt, encoding="utf-8") as fid:
            glove_w2v = {
                line.split()[0]: np.array(line.split()[1:]).astype(float)
                for line in fid.readlines() if line.split()[0] in voc
            }
        print(list(voc - glove_w2v.keys()))
        assert list(voc - glove_w2v.keys()) == ['__background__']
        pred_w2v = np.array([
            np.mean([glove_w2v[word] for word in name.split()], axis=0)
            if any(word in glove_w2v for word in name.split())
            else np.zeros(300)
            for name in predicates
        ])
        # Set background as the mean of other classes
        pred_w2v[-1] = np.mean(pred_w2v[:-1, :], axis=0)
        obj_w2v = np.array([
            np.mean([glove_w2v[word] for word in name.split()], axis=0)
            for name in objects
        ])
        with open(self._word2vec_json, 'w') as fid:
            json.dump({
                'predicates': pred_w2v.tolist(),
                'objects': obj_w2v.tolist()
            }, fid)

    def save_conceptnet_vectors(self, predicates, objects):
        """Build conceptnet dictionary of dataset vocabulary."""
        if not os.path.exists(self._glove_txt):
            self._download_glove()
        # Please replace the following line with your path.
        conceptNet_numberbatch_file_path = 'C:\\Users\\user\\Desktop\\final_project_external_knowledge\\prerequisites\\numberbatch-en-19.08.txt' 
        with open(conceptNet_numberbatch_file_path, encoding='utf-8') as fid:
            en_w2v = {
                line.split()[0]: np.array(line.split()[1:]).astype(float)
                for line in fid.readlines()
            }
        interpretations = {name: [name] for name in predicates + objects}
        interpretations.update({
            'in the front of': ['in front of'],
            'on the left of': ['left'],
            'on the right of': ['right'],
            'on the top of': ['on top of'],
            'park behind': ['parked', 'behind'],
            'park next': ['parked', 'next to'],
            'park on': ['parked', 'on'],
            'sit behind': ['sitting', 'behind'],
            'sit next to': ['sitting', 'next to'],
            'sit under': ['sitting', 'under'],
            'sleep next to': ['sleeping', 'next to'],
            'stand next to': ['stand', 'next to'],
            'stand under': ['stand', 'under'],
            'taller than': ['taller', 'than'],
            'walk beside': ['walk', 'beside'],
            'walk next to': ['walk', 'next to'],
            'walk past': ['walk', 'past']
        })
        pred_w2v = np.array([
            np.mean([
                en_w2v['_'.join(word.split())]
                for word in interpretations[name]
            ], axis=0)
            if name != '__background__'
            else np.zeros(300)
            for name in predicates
        ])
        # Set background as the mean of other classes
        pred_w2v[-1] = np.mean(pred_w2v[:-1, :], axis=0)
        obj_w2v = np.array([
            np.mean([
                en_w2v['_'.join(word.split())]
                for word in interpretations[name]
            ], axis=0)
            for name in objects
        ])
        # Normalize embeddings
        pred_w2v = pred_w2v / np.sqrt(np.sum(pred_w2v ** 2, axis=1))[:, None]
        obj_w2v = obj_w2v / np.sqrt(np.sum(obj_w2v ** 2, axis=1))[:, None]
        with open(self._conceptnet_json, 'w') as fid:
            json.dump({
                'predicates': pred_w2v.tolist(),
                'objects': obj_w2v.tolist()
            }, fid)

    @staticmethod
    def update_labels(annos, predicates, objects):
        """Update objects and predicates ids."""
        predicates = {pred: p for p, pred in enumerate(predicates)}
        objects = {obj: o for o, obj in enumerate(objects)}
        for anno in annos:
            anno['relations']['ids'] = [
                predicates[name] for name in anno['relations']['names']]
            anno['objects']['ids'] = [
                objects[name] for name in anno['objects']['names']]
        return annos

    def _compute_im_size(self, im_name):
        """Compute image size."""
        if not os.path.exists(self._orig_images_path + im_name):
            return None, None
        im_width, im_height = Image.open(self._orig_images_path + im_name).size
        return im_height, im_width

    @staticmethod
    def compute_relationship_probabilities(annos, predicates, objects):
        """
        Compute probabilities P(Pred|<Sub,Obj>) from dataset.

        The Laplacian estimation is used:
            P(A|B) = (N(A,B)+1) / (N(B)+V_A),
        where:
            N(X) is the number of occurences of X and
            V_A is the number of different values that A can have

        Returns a (n_obj, n_obj, n_rel) array where P[i,j,k] = P(k|i,j)
        """
        prob_matrix = np.ones((len(objects), len(objects), len(predicates)))
        for anno in annos:
            if anno['split_id'] == 0 and any(anno['relations']['names']):
                ids = np.array(anno['objects']['ids'])
                unique_triplets = np.unique(np.stack((
                    anno['relations']['subj_ids'],
                    anno['relations']['obj_ids'],
                    anno['relations']['ids']
                ), axis=1), axis=0)
                prob_matrix[
                    ids[unique_triplets[:, 0]],
                    ids[unique_triplets[:, 1]],
                    unique_triplets[:, 2]] += 1
        prob_matrix /= prob_matrix.sum(2)[:, :, None]
        return prob_matrix.tolist()

    def compute_class_merging(self, annos, predicates):
        """Estimate synonyms to merge during evaluation."""
        # Adjacency matrices only for existing pairs to reduce memory
        pairs = set(
            (subj, obj)
            for anno in annos
            if anno['relations']['ids']
            for subj, obj in zip(
                np.array(anno['objects']['ids'])[
                    np.array(anno['relations']['subj_ids'])
                ],
                np.array(anno['objects']['ids'])[
                    np.array(anno['relations']['obj_ids'])
                ]
            )
        )
        connections = {pair: np.eye(len(predicates)) for pair in pairs}
        # Fill adjacency matrices
        for anno in annos:
            relations = anno['relations']
            objects = anno['objects']['ids']
            pairs = np.stack(
                (relations['subj_ids'], relations['obj_ids']),
                axis=1
            )
            # Find pairs with same (subj_id, obj_id)
            common_pairs = (pairs[..., None] == pairs.T[None, ...]).all(1) * 1
            nz_i, nz_j = common_pairs.nonzero()
            for ind_i, ind_j in zip(nz_i, nz_j):
                connections[
                    objects[relations['subj_ids'][ind_i]],
                    objects[relations['obj_ids'][ind_j]]
                ][relations['ids'][ind_i], relations['ids'][ind_j]] = 1
        # Densify connections (a synomym of my synonym is also mine)
        for key, mat in connections.items():
            connections[key] = self._densify_mat(mat).argmax(1).tolist()
        return connections

    @staticmethod
    def _densify_mat(mat):
        """Densify matrices per pair."""
        old_mat = np.zeros_like(mat)
        while (mat != old_mat).any():
            old_mat = np.copy(mat)
            mat = np.minimum(mat + np.matmul(mat.T, mat), 1)
        return mat

    @staticmethod
    def update_annos_with_syns(annos, connections):
        """Create merged_ids field in relations."""
        for anno in annos:
            objects = anno['objects']['ids']
            anno['relations']['merged_ids'] = list([
                connections[objects[subj_id], objects[obj_id]][_id]
                for subj_id, obj_id, _id in zip(
                    anno['relations']['subj_ids'],
                    anno['relations']['obj_ids'],
                    anno['relations']['ids']
                )
            ])
        return annos

    def save_negative_json(self):
        """
        Mine negative examples for certain predicates using rules.

        (r1) Do not share: if (S1, P, O) then !(S2, P, O)
        (r2) Don't be shared: if (S, P, O1) then !(S, P, O2)
        """
        with open(self._predcls_json) as fid:
            annos = json.load(fid)
        negatives = {}
        for anno in annos:
            relations = anno['relations']
            neg_ids = [[] for _ in range(len(relations['ids']))]
            for n_cnt, name in enumerate(relations['names']):
                obj_id = relations['obj_ids'][n_cnt]
                subj_id = relations['subj_ids'][n_cnt]
                obj_box = anno['objects']['boxes'][obj_id]
                subj_box = anno['objects']['boxes'][subj_id]
                if name in self.r1_preds:
                    for r_cnt, rel in enumerate(relations['names']):
                        this_subj_id = relations['subj_ids'][r_cnt]
                        this_subj_box = anno['objects']['boxes'][this_subj_id]
                        rule_applies = (
                            this_subj_id != subj_id  # not S1
                            and compute_overlap(this_subj_box, subj_box) < 0.9
                            and rel != name  # not annotated with P
                            and relations['obj_ids'][r_cnt] == obj_id  # same O
                        )
                        if rule_applies:
                            neg_ids[r_cnt].append(relations['ids'][n_cnt])
                elif name in self.r2_preds:
                    for r_cnt, rel in enumerate(relations['names']):
                        this_obj_id = relations['obj_ids'][r_cnt]
                        this_obj_box = anno['objects']['boxes'][this_obj_id]
                        rule_applies = (
                            relations['subj_ids'][r_cnt] == subj_id  # same S
                            and rel != name  # not annotated with P
                            and this_obj_id != obj_id  # not O1
                            and compute_overlap(this_obj_box, obj_box) < 0.9
                        )
                        if rule_applies:
                            neg_ids[r_cnt].append(relations['ids'][n_cnt])
            negatives[anno['filename']] = neg_ids
        with open(self._negative_json, 'w') as fid:
            json.dump(negatives, fid)

    def _download_glove(self):
        """Download GloVe embeddings."""
        data_folder = '/'.join(self._glove_txt.split('/')[:-1]) + '/'
        os.system(
            "wget https://huggingface.co/stanfordnlp/glove/resolve/main/glove.42B.300d.zip"
        ) 
        # https://huggingface.co/stanfordnlp/glove/resolve/main/glove.42B.300d.zip
        # http://nlp.stanford.edu/data/wordvecs/glove.42B.300d.zip
        shutil.move('glove.42B.300d.zip', data_folder + 'glove.42B.300d.zip')
        with ZipFile(data_folder + 'glove.42B.300d.zip') as fid:
            fid.extractall(data_folder)
        os.remove(data_folder + 'glove.42B.300d.zip')

    @staticmethod
    def _transform_annotations(annos):
        """
        Transform relationship annotations.

        Returns a list of dicts:
            {
                'filename': filename (no path),
                'split_id': int, 0/1/2 for train/val/test,
                'height': int, image height in pixels,
                'width': int, image width in pixels,
                'objects': {
                    'names': list of str,
                    'boxes': list of 4-tuples
                },
                'relations': {
                    'names': list of str,
                    'subj_ids': list of int,
                    'obj_ids': list of int
                }
            }
        """
        objects = [
            {
                obj_tuple: o
                for o, obj_tuple in enumerate(sorted(list(
                    set(
                        (tuple(rel['subject_box']), rel['subject'])
                        for rel in anno['relationships']
                    ).union(set(
                        (tuple(rel['object_box']), rel['object'])
                        for rel in anno['relationships']
                    ))
                ), key=lambda t: (t[0][2] + t[0][3])))
            }
            for anno in annos
        ]
        inv_objects = [
            {v: k for k, v in obj_dict.items()} for obj_dict in objects]
        relationships = [
            {
                'names': [rel['predicate'] for rel in anno['relationships']],
                'subj_ids': [
                    obj_dict[(tuple(rel['subject_box']), rel['subject'])]
                    for rel in anno['relationships']],
                'obj_ids': [
                    obj_dict[(tuple(rel['object_box']), rel['object'])]
                    for rel in anno['relationships']]
            }
            for anno, obj_dict in zip(annos, objects)
        ]
        return [
            {
                'filename': anno['filename'],
                'split_id': anno['split_id'],
                'height': anno['height'],
                'width': anno['width'],
                'objects': {
                    'names': [objs[o][1] for o in sorted(objs.keys())],
                    'boxes': [objs[o][0] for o in sorted(objs.keys())]
                },
                'relations': relations
            }
            for anno, relations, objs in zip(annos, relationships, inv_objects)
        ]


def compute_area(bbox):
    """Compute area of box 'bbox' ([y_min, y_max, x_min, x_max])."""
    return max(0, bbox[3] - bbox[2] + 1) * max(0, bbox[1] - bbox[0] + 1)


def compute_overlap(bbox0, bbox1):
    """Compute overlap (<=1) between two boxes."""
    intersection_bbox = [
        max(bbox0[0], bbox1[0]),
        min(bbox0[1], bbox1[1]),
        max(bbox0[2], bbox1[2]),
        min(bbox0[3], bbox1[3])
    ]
    intersection_area = compute_area(intersection_bbox)
    union_area = (compute_area(bbox0)
                  + compute_area(bbox1)
                  - intersection_area)
    return intersection_area / union_area
