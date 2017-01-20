"""
    Implementation of the core functions for the Structure-awared FMs in TensorFlow

    Author: Chun-Ta Lu <clu29@uic.edu>
"""
from __future__ import (absolute_import, division,
                                print_function, unicode_literals)
#from builtins import *
import tensorflow as tf
# from . import utils
import math


class SFMCore():
    """
    This class underlying routine about creating computational graph.
    Its required n_features to be set at graph building time.

    Parameters
    ----------
    view_list: list of int tuple
        # index starting from 1
        modes in each view structure, e.g., [(1,2,3),(1,4)] 
        represents view1 consists of the tensor structure of mode1, mode2, mode3
        view 2 consist of the matrix of mode1 and mode4
        the number of modes is the max value in the tuple

    co_rank : int
        Number of common factors in low-rank appoximation.
        Shared by all the modes. 

    view_rank: int
        Number of view-discriminative factors in low-rank appoximation.
        Shared by all the modes.

    input_type : str, 'dense' or 'sparse', default: 'dense'
        Type of input data. Only numpy.array allowed for 'dense' and
        scipy.sparse.csr_matrix for 'sparse'. This affects construction of
        computational graph and cannot be changed during training/testing.

    optimizer : tf.train.Optimizer, default: AdamOptimizer(learning_rate=0.1)
        Optimization method used for training

    reg_type: str
        'L1', 'L2', 'L21', 'maxNorm' are supported, default: 'L2'

    reg : float, default: 0
        Strength of regularization

    init_scaling : float, default: 2.0
        Amplitude of random initialization
        The factor augment in tf.contrib.layers.variance_scaling_initializer()
        http://www.tensorflow.org/api_docs/python/contrib.layers/initializers#variance_scaling_initializer

    Attributes
    ----------
    graph : tf.Graph or None
        Initialized computational graph or None

    trainer : tf.Op
        TensorFlow operation node to perform learning on single batch

    n_feature_list : list of int
        Number of features in each mode used in this dataset.
        Inferred during the first call of fit() method.

    saver : tf.Op
        tf.train.Saver instance, connected to graph

    summary_op : tf.Op
        tf.merge_all_summaries instance for export logging

    b : list of tf.Variable, shape: [mode]
        Bias term for each mode

    W : list of tf.Variable, shape: [n_mode][n_view]
        List of underlying representations.
        First element in each mode will have shape [n_feature_list[mode], co_rank]
        all the others -- [n_feature_list[mode], view_rank].

    Notes
    -----
    
    tf.sparse_reorder doesn't requied since COO format is lexigraphical ordered.
    This implementation uses a generalized approach from referenced paper along
    with caching.

    References
    ----------

    """
    def __init__(self, view_list, co_rank, view_rank, isFullOrder, input_type, output_range,
                    loss_function, optimizer, reg_type, reg, init_scaling):
        self.view_list = view_list
        self.co_rank = co_rank
        self.view_rank = view_rank
        self.input_type = input_type
        self.output_range = output_range
        self.loss_function = loss_function
        self.optimizer = optimizer
        self.reg_type = reg_type
        self.reg = reg
        self.init_scaling = init_scaling
        self.n_modes = max([x for v in view_list for x in v ])
        self.n_views = len(view_list)
        self.n_feature_list = None
        self.mode_matrices = None
        self.graph = None
        self.isRelational = False
        self.isFullOrder = isFullOrder

    def set_relational_input(self, isRelational):
        assert isinstance(isRelational,bool)
        self.isRelational = isRelational

    def set_num_features(self, n_feature_list):
        self.n_feature_list = n_feature_list

    def _init_learnable_params(self):
        self.W = [[None] * self.n_modes for i in range(self.n_views + 1)]
        self.Bias = [[None] * self.n_modes for i in range(self.n_views + 1)]
        self.S = [None] * self.n_modes
        r = self.co_rank + self.view_rank

#        if self.reg_type == 'L1':
            #regular = tf.contrib.layers.l1_regularizer(self.reg)
        #else:
            #regular = tf.contrib.layers.l2_regularizer(self.reg)

        # initialize factors for each view
        # to avoid the multiplication close to zero when the views has more than 3 modes
        # we can try to scaling the unfiorm distribution using variance_scaling_initializer
#        if self.n_views > 1:
            #self.Phi = tf.get_variable('embedding_phi', shape = [r, self.n_views+1], trainable=True,
                                    #initializer = tf.contrib.layers.variance_scaling_initializer(factor= self.init_scaling))
        #else:
        self.Phi = tf.get_variable('embedding_phi', shape = [r, self.n_views], trainable=True,
                                    initializer = tf.contrib.layers.variance_scaling_initializer(factor= self.init_scaling))
#                                    initializer = tf.contrib.layers.xavier_initializer())

        #Phi = tf.nn.l2_normalize(Phi, dim=0)
#        self.Phi = tf.Variable(Phi, name= 'embedding_phi')

        self.b = tf.Variable(0.0, trainable=True, name='b')
        # initialize shared factors for each mode
        for m in range(self.n_modes):
            with tf.variable_scope('co_mode_'+str(m+1)):
                self.W[0][m] = tf.get_variable('embedding_init',
                           shape = [self.n_feature_list[m], self.co_rank],
                           trainable=True,
                           initializer = tf.contrib.layers.variance_scaling_initializer(factor = self.init_scaling))
#                            initializer = tf.contrib.layers.xavier_initializer())
                #W = tf.nn.l2_normalize(W, dim=0)
                self.S[m] = tf.get_variable('layer_norm_S', initializer = tf.ones([r]))
                #self.W[0][m] = tf.Variable(W, name = 'embedding')

        # initialize view specific facotrs for each mode
        for i, modes in enumerate(self.view_list):
            v = i+1
            for m in set(modes):
                with tf.variable_scope('view_'+str(v)+'_mode_' + str(m)):
                    try:
                        self.Bias[v][m-1] = tf.get_variable('bias',
                                trainable = self.isFullOrder,
                                initializer=tf.zeros_initializer([1, r]))
                    except:
                        print('bias mode {} shared in view {}'.format(m,v))
                    try:
                        if self.view_rank>0:
                            self.W[v][m-1] = tf.get_variable('embedding_init',
                               shape = [self.n_feature_list[m-1], self.view_rank],
                               trainable=True,
                                initializer = tf.contrib.layers.variance_scaling_initializer(factor = init_scaling))
#                               initializer = tf.contrib.layers.xavier_initializer())
                            #W = tf.nn.l2_normalize(W, dim=0)
                            #self.W[v+1][m-1] = tf.Variable(W, name = 'embedding')
                    except:
                        print('mode {} shared in view {}'.format(m,v))


    def _init_placeholders(self):
        self.train_x = [None]*self.n_modes
        if self.isRelational:
            self.mode_matrices = [None] * self.n_modes

        #sparse case
        if self.input_type != 'dense':
            self.raw_indices = [None]*self.n_modes
            self.raw_values = [None]*self.n_modes
            self.raw_shape = [None]*self.n_modes

        for i in range(self.n_modes):
            with tf.variable_scope('mode_'+str(i+1)):
                # if given mode matrix, the input X_ is the list of the row indicators of the mode matrix
                if self.input_type == 'dense':
                    if self.isRelational:
                        self.train_x[i] = tf.placeholder(tf.int64, shape=[None], name='X_indices')
                        self.mode_matrices[i] = tf.placeholder(tf.float32, shape=[None, self.n_feature_list[i]], name='X_matrix')
                    else:
                        self.train_x[i] = tf.placeholder(tf.float32, shape=[None, self.n_feature_list[i]], name='X')
                else:
                    #sparse case
                    self.raw_indices[i] = tf.placeholder(tf.int64, shape=[None, 2], name='raw_indices')
                    self.raw_values[i] = tf.placeholder(tf.float32, shape=[None], name='raw_data')
                    self.raw_shape[i] = tf.placeholder(tf.int64, shape=[2], name='raw_shape')
                    if self.isRelational:
                        self.train_x[i] = tf.placeholder(tf.int64, shape=[None], name='X_indices')
                        self.mode_matrices[i] = tf.SparseTensor(self.raw_indices[i], self.raw_values[i], self.raw_shape[i])
                    # tf.sparse_reorder is not needed since scipy return COO in canonical order
                    else:
                        self.train_x[i] = tf.SparseTensor(self.raw_indices[i], self.raw_values[i], self.raw_shape[i])
        self.train_y = tf.placeholder(tf.float32, shape=[None], name='Y')
    def _batch_norm(self, Z, s, b):
        eps = 1e-5
        # Calculate batch mean and variance
        m, v = tf.nn.moments(Z, [0], keep_dims = True)

        # Apply the initial batch normalizing transform
        normalized_Z = (Z - m) / tf.sqrt(v + eps)
        return normalized_Z * s + b

    def _layer_norm(self, Z, s, b):
        eps = 1e-5
        m, v = tf.nn.moments(Z, [1], keep_dims = True)
        normalized_Z = (Z - m) / tf.sqrt(v + eps)
        return normalized_Z * s + b

#    def _pow_matmul(self, order, pow):
        #if pow not in self.x_pow_cache:
            #x_pow = pow_wrapper(self.train_x, pow, self.input_type)
            #self.x_pow_cache[pow] = x_pow
        #if order not in self.matmul_cache:
            #self.matmul_cache[order] = {}
        #if pow not in self.matmul_cache[order]:
            #w_pow = tf.pow(self.w[order - 1], pow)
            #dot = matmul_wrapper(self.x_pow_cache[pow], w_pow, self.input_type)
            #self.matmul_cache[order][pow] = dot
        #return self.matmul_cache[order][pow]

    def _regularizer_func(self, W, node_name):
        if self.reg_type == 'L1':
            norm = tf.reduce_sum(tf.abs(W), name=node_name)
        else:
            norm = tf.nn.l2_loss(W, name=node_name)
        return norm

    def _init_regular(self):
        self.regularization = 0
        tf.scalar_summary('bias', self.b)

        self.regularization = 0
        for m in range(self.n_modes):
            node_name = 'regularization_penalty_v0_m{}'.format(m)
            norm = self._regularizer_func(self.W[0][m],node_name)
            tf.scalar_summary('norm_W_v0_m{}'.format(m), norm)
            self.regularization += norm
        for i, modes in enumerate(self.view_list):
            v = i + 1
            for m in set(modes):
                try:
                    node_name = 'regularization_penalty_v{}_b{}'.format(v, m)
                    norm = self._regularizer_func(self.Bias[v][m-1], node_name)
                    tf.scalar_summary('norm_Bias_v{}_m{}'.format(v,m), norm)
                except:
                    print('bias mode {} shared in view {}'.format(m,v))
                self.regularization += norm
                if self.view_rank > 0:
                    try:
                        node_name = 'regularization_penalty_v{}_m{}'.format(v,m)
                        norm = self._regularizer_func(self.W[v][m-1],node_name)
                        tf.scalar_summary('norm_W_v{}_m{}'.format(v,m), norm)
                    except:
                        print('mode {} shared in view {}'.format(m,v))
                    self.regularization += norm

        for v in range(len(self.view_list)):
            norm = self._regularizer_func(self.Phi[:,v], 'regularization_penalty_phi{}'.format(v+1))
            tf.scalar_summary('norm_Phi_v{}'.format(v+1), norm)
        node_name = 'regularization_penalty_phi'
        norm = self._regularizer_func(self.Phi, node_name)
        self.regularization += norm
        tf.scalar_summary('regularization_penalty', self.regularization)

#    def _norm_constraint_op(self):
        #scaling = 0
        #for v in range(len(self.view_list)):
            #norm = tf.nn.l2_loss()
            #norm = self._regularizer_func(self.Phi[:,v], 'regularization_penalty_phi{}'.format(v+1))
            #tf.scalar_summary('norm_Phi_v{}'.format(v+1), norm)
        #return tf.assign(self.Phi, scaling)
#   def _norm_constraint_op(self):
        #assign_op = []
        #scaling_mode = [None] * self.n_modes
        #eps = 1e-10
        #for m in range(self.n_modes):
            ## normalize every column in the weight matrix to norm_l2 = 1
            #scaling_mode[m] = tf.sqrt(tf.reduce_sum(tf.square(self.W[0][m]), 0), name = 'scaling_v0_m{}'.format(m)) + eps
            #norm = tf.reduce_sum(scaling_mode[m])
            #tf.scalar_summary('norm_W_v0_m{}'.format(m), norm)

            #scaled = self.W[0][m] / tf.expand_dims(scaling_mode[m], 0)
            #assign_op.append(tf.assign(self.W[0][m], scaled))

        #prod_scaling = [None] * self.n_views
        #for i, modes in enumerate(self.view_list):
            #v = i + 1
            #with tf.name_scope('view_{}'.format(v)) as scope:
                #scaling_list = [None] * self.n_modes
                ## noting that the modes given in the input start from 1
                #for m in modes:
                    #with tf.name_scope('mode_{}'.format(m)) as scope:
                        #if self.view_rank > 0:
                            #scaling_cache = tf.sqrt(tf.reduce_sum(tf.square(self.W[v][m-1]), 0)) + eps
                            #scaled = self.W[v][m-1] / tf.expand_dims(scaling_cache, 0)
                            #assign_op.append(tf.assign(self.W[v][m-1], scaled))
                            #scaling_list[m-1] = tf.concat(1, [scaling_mode[m-1], scaling_cache], name='scaling')
                        #else:
                            #scaling_list[m-1] = scaling_mode[m-1]
                #scaling_tensor = tf.pack([s for s in scaling_list if s is not None],axis=1)
                ## scaling Phi
                #prod_scaling[i] = tf.reduce_prod(scaling_tensor, reduction_indices=[1], name='prod_scaling')
        #phi_scaling = tf.pack([s for s in prod_scaling if s is not None], axis = 1)
        #print(phi_scaling)
        #scaled = tf.mul(self.Phi, phi_scaling)
        #print(self.Phi,scaled)
        #assign_op.append(tf.assign(self.Phi, scaled))
##        for i, modes in enumerate(self.view_list):
            ##v = i + 1
            ##norm = tf.nn.l2_loss(self.Phi[:,i])
            ##tf.scalar_summary('norm_Phi_v{}'.format(v), norm)
        ##print(assign_op)
        #return assign_op


    def _init_loss(self):
        self.loss = self.loss_function(self.outputs, self.train_y)
        self.reduced_loss = tf.reduce_mean(self.loss)
        tf.scalar_summary('loss', self.reduced_loss)

    def _init_main_block(self):
        self.prod_view = {}
        r = self.co_rank + self.view_rank

#        self.outputs = self.b
        self.outputs = 0
        train_shape = [tf.shape(self.train_x[0])[0], r]
        self.XW_cache = {}
        self.prod_embedding = [None] * self.n_views
        self.view_contribution = [None] * self.n_views

        for m in range(self.n_modes):
            self.XW_cache[m] = self._view_mode_embedding(0, m)
#        print(self.XW_cache)

        for i, modes in enumerate(self.view_list):
            v = i + 1
            with tf.name_scope('view_{}'.format(v)) as scope:
                XW_list = [None] * self.n_modes
                # noting that the modes given in the input start from 1
                for m in modes:
                    with tf.name_scope('mode_{}'.format(m)) as scope:
                        if self.view_rank > 0:
                            XW = self._view_mode_embedding(v, m - 1)
                            XW_list[m-1] = tf.concat(1, [self.XW_cache[m-1], XW], name='XW')
                        else:
                            XW_list[m-1] = self.XW_cache[m-1]
                        XW_list[m-1] += self.Bias[v][m-1]
#                        XW_list[m-1] = self._batch_norm(XW_list[m-1], self.S[m-1], self.Bias[v][m-1])
#                        XW_list[m-1] = self._layer_norm(XW_list[m-1], self.S[m-1], self.Bias[v][m-1])

                # the reduction_indices in the reduce_prod does not handle scalar, 
                # so we need to transform it to a tensor
                embedding_tensor = tf.pack([xw for xw in XW_list if xw is not None],axis=2, name='embedding_tensor')
                self.prod_embedding[i] = tf.reduce_prod(embedding_tensor, reduction_indices=[2], name='prod_embedding')
#                view_prod_sum = tf.reduce_sum(self.prod_embedding[i], reduction_indices=[1], name='view_prod_sum')
#                tf.histogram_summary('view_prod_sum{}'.format(v), view_prod_sum)

#                if self.n_views > 1: 
                    #view_contrib = matmul_wrapper(self.prod_embedding[i], tf.reshape(self.Phi[:,0],(r,1)), 'dense')
                    #self.view_contribution[i] = view_contrib + matmul_wrapper(self.prod_embedding[i], tf.reshape(self.Phi[:,v],(r,1)), 'dense')
                    #tf.histogram_summary('view_contribution{}'.format(v), self.view_contribution[i])
                #else:
                    #self.view_contribution[i] = matmul_wrapper(self.prod_embedding[i], tf.reshape(self.Phi[:,i],(r,1)), 'dense')
                #tf.histogram_summary('view_contribution{}'.format(v), self.view_contribution[i])
                self.view_contribution[i] = matmul_wrapper(self.prod_embedding[i], tf.reshape(self.Phi[:,i],(r,1)), 'dense')
                tf.histogram_summary('view_contribution{}'.format(v), self.view_contribution[i])

        self.outputs += tf.reduce_sum(self.view_contribution, reduction_indices=[0], name='output')
        tf.histogram_summary('output', self.outputs)

        with tf.name_scope('loss') as scope:
            self._init_loss()

        with tf.name_scope('regularization') as scope:
            self._init_regular()

    def _view_mode_embedding(self, v, m):
        if self.isRelational:
            modeEmbedding = matmul_wrapper(self.mode_matrices[m], self.W[v][m], self.input_type)
            XW = tf.nn.embedding_lookup(modeEmbedding, self.train_x[m])
        else:
            XW = matmul_wrapper(self.train_x[m], self.W[v][m], self.input_type)
        return XW


    def _init_target(self):
#        reg_losses = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
        self.target = self.reduced_loss + self.reg * self.regularization
#        self.target = self.reduced_loss

#        self.target = tf.Print(self.target, [self.reduced_loss, self.target], message="loss, target: ")
        self.checked_target = tf.verify_tensor_all_finite(
            self.target,
            msg='NaN or Inf in target value', name='target')
        tf.scalar_summary('target', self.checked_target)

    def build_graph(self):
        """Build computational graph according to params."""
        assert self.n_feature_list is not None
        self.graph = tf.Graph()
        with self.graph.as_default():
            with tf.name_scope('params') as scope:
                self._init_learnable_params()

            with tf.name_scope('inputBlock') as scope:
                self._init_placeholders()

            with tf.name_scope('mainBlock') as scope:
                self._init_main_block()

            self._init_target()

            self.trainer = self.optimizer.minimize(self.checked_target)
            self.init_all_vars = tf.initialize_all_variables()
#            self.post_step = self._norm_constraint_op()
            self.summary_op = tf.merge_all_summaries()
            self.saver = tf.train.Saver()

def matmul_wrapper(A, B, optype):
    """Wrapper for handling sparse and dense versions of matmul operation.

    Parameters
    ----------
    A : tf.Tensor
    B : tf.Tensor
    optype : str, {'dense', 'sparse'}

    Returns
    -------
    tf.Tensor
    """

    if optype == 'dense':
        return tf.matmul(A, B)
    elif optype == 'sparse':
        return tf.sparse_tensor_dense_matmul(A, B)
    else:
        raise NameError('Unknown input type in matmul_wrapper')

#def L2Ball_update(var_matrix, maxnorm=1.0):
    #'''Dense update operation that ensures all columns in var_matrix 
        #have a Euclidean norm equal to maxnorm. 

    #Args:
        #var_matrix: 2D mutable tensor (Variable) to operate on
        #maxnorm: the maximum Euclidean norm
        
    #Returns:
        #An operation that will update var_matrix when run in a Session
    #'''
    #scaling = tf.sqrt(tf.reduce_sum(tf.square(var_matrix), 0))
    #scaled = var_matrix / tf.expand_dims(scaling, 1)
    #return tf.assign(var_matrix, scaled)


#def L2Ball(var_matrix, maxnorm=1.0):
    #'''Similar to L2Ball_update(), except this returns a new Tensor
       #instead of an operation that modifies var_matrix.

    #Args:
        #var_matrix: 2D tensor (Variable)
        #maxnorm: the maximum Euclidean norm

    #Returns:
        #A new tensor where all rows have been scaled as necessary
    #'''
    #scaling = tf.sqrt(tf.reduce_sum(tf.square(var_matrix), 0))
    #return var_matrix / tf.expand_dims(scaling, 0)

#def pow_wrapper(X, p, optype):
    #"""Wrapper for handling sparse and dense versions of power operation.

    #Parameters
    #----------
    #X : tf.Tensor
    #p : int
    #optype : str, {'dense', 'sparse'}

    #Returns
    #-------
    #tf.Tensor
    #"""
    #if optype == 'dense':
        #return tf.pow(X, p)
    #elif optype == 'sparse':
        #return tf.SparseTensor(X.indices, tf.pow(X.values, p), X.shape)
    #else:
        #raise NameError('Unknown input type in pow_wrapper')