import numpy as np
import os,sys
import tensorflow as tf
import time
from datetime import datetime
import os
import os.path as osp
import sklearn.metrics as metrics
import cv2, random

import config
import similarity
import model
import IPython

from scipy import spatial


from data import Dataset, TripletData, PairData
import data
import shutil

flags = tf.compat.v1.app.flags

flags.DEFINE_string('feature', 'fc6', 'Extract which layer(pool5, fc6, fc7)')
flags.DEFINE_integer('batch_size', 64, 'Value of batch size')
flags.DEFINE_boolean('remove', False, 'Remove invalid triplet or not')
flags.DEFINE_float('enlarge', 1, 'Enlarge ground truth')
flags.DEFINE_float('lr', 0.000005, 'learing rate')
flags.DEFINE_boolean('da', False, 'Data augmentation')
flags.DEFINE_string('train_dir', 'frame/all', 'Directory path of training data')
flags.DEFINE_string('test_dir', 'frame/all', 'Directory path of testing data')
flags.DEFINE_boolean('isdrone', True, 'is drone data')
flags.DEFINE_string('model_dir', 'best_30', 'model path') #ig_21.61

max_epo = 20
SAVE_INTERVAL = 1
print_iter = 10000 # > batch size

random.seed(1223)

FLAGS = flags.FLAGS
 #cross_mean_fc6
parameter_name = osp.join(FLAGS.train_dir, "cross_mean_fc6",
    "{}/{}/{}/{}/{}/{}".format(FLAGS.feature,
    FLAGS.batch_size, FLAGS.lr, FLAGS.da, FLAGS.enlarge, FLAGS.remove))

def modelpath(model_name):
    return "model/{}/{}".format(parameter_name, model_name)

def finish_training(saver, sess, epoch):
    print(("finish training at {}".format(epoch)))
    saver.save(sess, modelpath("final_{}".format(epoch)))


def train(dataset_train, dataset_val, dataset_test, ckptfile='', caffemodel=''):
    print('Training start...')
    batch_size = FLAGS.batch_size



    path = modelpath("")
    if not os.path.exists(path):
        os.makedirs(path)    


    with tf.Graph().as_default():

        startstep = 0 #if not is_finetune else int(ckptfile.split('-')[-1])
        global_step = tf.Variable(startstep, trainable=False)
         
        # placeholders for graph input

        anchor_search = tf.compat.v1.placeholder('float32', shape=(None, 227, 227, 3))
        anchor_street = tf.compat.v1.placeholder('float32', shape=(None, 227, 227, 3))
        anchor_aerial = tf.compat.v1.placeholder('float32', shape=(None, 227, 227, 3))

        positive = tf.compat.v1.placeholder('float32', shape=(None, 227, 227, 3))
        negative = tf.compat.v1.placeholder('float32', shape=(None, 227, 227, 3))

        keep_prob_ = tf.compat.v1.placeholder('float32')

        # graph outputs
        feature_anchor   = model.inference_crossview([anchor_search, anchor_street, anchor_aerial],
         keep_prob_, FLAGS.feature, False)
        feature_positive = model.inference(positive, keep_prob_, FLAGS.feature)
        feature_negative = model.inference(negative, keep_prob_, FLAGS.feature)
        
        feature_size = tf.size(feature_anchor)/batch_size


        feature_list = model.feature_normalize(
            [feature_anchor, feature_positive, feature_negative])


        loss, d_pos, d_neg, loss_origin = model.triplet_loss(feature_list[0], feature_list[1], feature_list[2])


        # summary
        summary_op = tf.merge_all_summaries()

        training_loss = tf.compat.v1.placeholder('float32', shape=(), name='training_loss')
        training_summary = tf.scalar_summary('training_loss', training_loss)

        optimizer = tf.compat.v1.train.AdamOptimizer(learning_rate = FLAGS.lr).minimize(loss) #batch size 512
        #optimizer = tf.train.AdamOptimizer(learning_rate = 0.0000001).minimize(loss)
        

        #validation
        validation_loss = tf.compat.v1.placeholder('float32', shape=(), name='validation_loss')
        validation_summary = tf.scalar_summary('validation_loss', validation_loss)

        # test

        feature_pair_list = model.feature_normalize(
            [feature_anchor, feature_positive])

        pair_loss = model.eval_loss(feature_pair_list[0], feature_pair_list[1])
        testing_loss = tf.compat.v1.placeholder('float32', shape=(), name='testing_loss')
        testing_summary = tf.scalar_summary('testing_loss', testing_loss)


        init_op = tf.compat.v1.initialize_all_variables()

        config = tf.compat.v1.ConfigProto()
        config.gpu_options.allow_growth=True

        with tf.compat.v1.Session(config=config) as sess:


            saver = tf.compat.v1.train.Saver(max_to_keep=50)
            if ckptfile:
                # load checkpoint file
                saver.restore(sess, ckptfile)
                """
                sess.run(init_op)
                
                all_vars = tf.all_variables()
                cv_vars = [k for k in all_vars if k.name.startswith("cv_")]
                share_vars = [k for k in all_vars if not k.name.startswith("cv_")]

                saver_share = tf.train.Saver(share_vars)

                saver_share.restore(sess, ckptfile)
                with tf.variable_scope('fc6', reuse=True):
                    w = tf.get_variable('weights')
                    b = tf.get_variable('biases')

                with tf.variable_scope('cv_fc6', reuse=True):
                    for subkey, data in zip(('weights', 'biases'), (w, b)):
                        print 'loading cv_fc6', subkey
                        var = tf.get_variable(subkey)
                        sess.run(var.assign(data))

                """
                print ('restore variables done')
            elif caffemodel:
                # load caffemodel generated with caffe-tensorflow
                sess.run(init_op)
                model.load_alexnet(sess, caffemodel)
                print ('loaded pretrained caffemodel:', caffemodel)
            else:
                # from scratch
                sess.run(init_op)
                print ('init_op done')
            

            summary_writer = tf.train.SummaryWriter("logs/{}/{}/{}".format(
                FLAGS.train_dir, FLAGS.feature, parameter_name),
                                                graph=sess.graph)


            epoch = 1
            global_step = step = print_iter_sum =0
            min_loss = min_test_loss = sys.maxsize
            loss_sum = []

            while True:

                batch_x, batch_y, batch_z, isnextepoch, start, end = dataset_train.sample_path2img(batch_size, True)
                
                step += len(batch_y)
                global_step += len(batch_y)
                print_iter_sum += len(batch_y)
                    
                feed_dict = {anchor_search: batch_x['search'],
                             anchor_street: batch_x['streetview_clean'],
                             anchor_aerial: batch_x['aerial_clean'],
                             positive: batch_y,
                             negative: batch_z,
                             keep_prob_: 0.5 } # dropout rate

                _, loss_value, pos_value, neg_value, origin_value, anchor_value= sess.run(
                        [optimizer, loss, d_pos, d_neg, loss_origin, feature_list[0]],
                        feed_dict=feed_dict)
                loss_value = np.mean(loss_value)
                loss_sum.append(loss_value)

                if print_iter_sum/print_iter >= 1:
                    loss_sum = np.mean(loss_sum)
                    
                    print(('epo{}, {}/{}, loss: {}'.format(
+                        epoch, step, len(dataset_train.data), loss_sum)))
                    print_iter_sum -= print_iter
                    loss_sum = []

                loss_valuee = sess.run(training_summary,
                                        feed_dict={training_loss: loss_value})

                summary_writer.add_summary(loss_valuee, global_step)
                summary_writer.flush()

                action = 0
                if FLAGS.remove and loss_value == 0:
                    action = dataset_train.remove(start, end)
                    if action == 1:
                        finish_training(saver, sess, epoch)
                        break                        


                if isnextepoch or action == -1:

                    val_loss_sum = []
                    isnextepoch = False # set for validation 
                    step = 0
                    print_iter_sum = 0

                    # validation
                    while not isnextepoch:

                        val_x, val_y, val_z, isnextepoch, start, end = dataset_val.sample_path2img(batch_size, True)
                        val_feed_dict = { 
                                        anchor_search: val_x['search'],
                                        anchor_street: val_x['streetview_clean'],
                                        anchor_aerial: val_x['aerial_clean'],
                                        positive: val_y,
                                        negative: val_z,
                                        keep_prob_: 1.
                                    }
                        val_loss = sess.run([loss], feed_dict=val_feed_dict)
                        val_loss_sum.append(np.mean(val_loss))

                    dataset_val.reset_sample()
                    val_loss_sum = np.mean(val_loss_sum)
                    print(("Validation loss: {}".format(val_loss_sum)))

                    summary_val_loss_sum = sess.run(validation_summary,
                                                feed_dict={validation_loss: val_loss_sum})
                    summary_writer.add_summary(summary_val_loss_sum , global_step)

                    # testing
                    #IPython.embed()
                    num = 50
                    test_feed_dict = { 
                                       anchor_search: dataset_test[0]['search'][:num],
                                       anchor_street: dataset_test[0]['streetview_clean'][:num],
                                       anchor_aerial: dataset_test[0]['aerial_clean'][:num], 
                                       positive: dataset_test[1][:num],
                                       negative: dataset_test[0]['search'][:num], # useless
                                       keep_prob_: 1.
                                    }

                    test_loss = sess.run([pair_loss], feed_dict=test_feed_dict)
                    test_loss = np.mean(test_loss)
                    print(("Testing loss: {}".format(test_loss)))                    
                    summary_test_loss = sess.run(testing_summary,
                        feed_dict={testing_loss: test_loss})
                    summary_writer.add_summary(summary_test_loss, global_step)


                    # ready to flush
                    summary_str = sess.run(summary_op, feed_dict=feed_dict)
                    summary_writer.add_summary(summary_str, global_step)
                    summary_writer.flush()
                    # save by testing
                    if min_test_loss > test_loss:
                        min_test_loss = test_loss
                        """
                        if 'best_test_path' in locals():
                            os.remove(best_test_path)
                        """
                        best_test_path = modelpath(
                            "test_{}_{}".format(epoch, test_loss))
                        saver.save(sess, best_test_path)
                        print(best_test_path)
                    
                    # save by validation
                    elif min_loss > val_loss_sum:
                        min_loss = val_loss_sum
                        """
                        if 'best_path' in locals():
                            os.remove(best_path)
                        """
                        best_path = modelpath(
                            "val_{}_{}".format(epoch, val_loss_sum))
                        saver.save(sess, best_path)
                        print(best_path)
                    # save by SAVE_INTERVAL
                    elif epoch % SAVE_INTERVAL == 0:
                        path = modelpath(epoch)
                        saver.save(sess, path)
                        print(path)    

                    dataset_train.reset_sample()
                    print(epoch)


                    epoch += 1
                    if epoch >= max_epo:
                        finish_training(saver, sess, epoch)
                        break   



def create_triplet():
    print("Loading training data...")
    train_data = []
    landmark_root = FLAGS.train_dir
    save_dir  = "aug"
    print(("remove and make new directory {}".format(save_dir)))

    landmarks = {}
    pos_num = 10 # # of one images

    for landmark_dir in os.listdir(landmark_root):
        for img_name in os.listdir(osp.join(landmark_root, landmark_dir)):
            if not landmark_dir in landmarks:
                landmarks[landmark_dir] = []

            landmarks[landmark_dir].append(osp.join(landmark_root, landmark_dir, img_name))

    for landmark in landmarks:
        for img in landmarks[landmark]:

            positive = landmarks[landmark][:]
            positive.remove(img)
            negative = dict(landmarks)
            negative.pop(landmark)
            #pos_num = len(positive) if len(positive)<pos_num else pos_num
            pos_num = len(positive)
            
            for _ in range(pos_num):
                img_pos = positive.pop(random.randrange(len(positive)))
                img_neg = random.choice(negative[random.choice(list(negative.keys()))])                
                train_data.append(TripletData(img, img_pos, img_neg))
                sys.stdout.write("\r{:8d}".format(len(train_data)))
                sys.stdout.flush()


    random.shuffle(train_data)
    print(("\nFinish loading... size of training data: {}".format(len(train_data))))
    
    # validation on training data
    val_ratio = 1./7
    split_index = int(len(train_data) *val_ratio)

    return Dataset(train_data[split_index:]), Dataset(train_data[:split_index])
    
def create_triplet_drone():
    print("Loading training data...")
    train_data = []

    frame_dir = FLAGS.train_dir

    poi_dir   = "poi"
    bb_dir    = "faster_bb"

    query_list = ["search", "streetview_clean", "aerial_clean"]

    temp_dir = osp.join("temp", parameter_name)

    negative_threshold = 0.3

    query = {}


    #print("remove and make new directory {}".format(temp_dir))
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    for query_dir in query_list:
        query_path = osp.join(temp_dir, query_dir)
        if not os.path.exists(query_path):
            os.makedirs(query_path)
        for img_name in os.listdir(query_dir):
            gps = img_name.replace(".jpg","").replace(".png","")
            query[gps] = cv2.imread(osp.join(query_dir, img_name), cv2.IMREAD_COLOR)
            cv2.imwrite(osp.join(query_path, img_name), data.transform_img(query[gps]))

    for img_name in os.listdir(frame_dir):
        im = cv2.imread(osp.join(frame_dir, img_name), cv2.IMREAD_COLOR)
        img_name = img_name.replace(".jpg", "").replace(".png", "")

        negative_dir = osp.join(temp_dir, img_name,"negative_dir")

        if not os.path.exists(negative_dir):
            os.makedirs(negative_dir)

        # write all proposal first
        with open( osp.join(bb_dir, img_name+".txt"), 'r') as ff:
            for linee in ff:
                token = linee.strip().split()
                bb = [int(float(token[0])), int(float(token[1])), 
                int(float(token[2])), int(float(token[3]))]

                cv2.imwrite(osp.join(negative_dir, 
                 "{}_{}_{}_{}.jpg".format(bb[0], bb[1], bb[2], bb[3])),
                 data.transform_img(im[bb[1]:bb[3], bb[0]:bb[2]]))

        with open(osp.join(poi_dir, img_name+".txt"), 'r') as f:
            for line in f:
                token = line.strip().split("\t")
                gps_pos = [0, 0]
                [name, gps_pos[0], gps_pos[1], google_type, img_ref, gt] = token
                str_gps_pos = [gps_pos[0], gps_pos[1]]
                query_name = str(str_gps_pos[0])+'_'+str(str_gps_pos[1])
                

                gt = gt.split(',')
                gt = [int(i) for i in gt]
                positive = im[gt[1]:gt[3], gt[0]:gt[2]]
                positive_dir = "{}_{}_{}_{}".format(gt[0], gt[1], gt[2], gt[3])

                negative_list = []

                with open( osp.join(bb_dir, img_name+".txt"), 'r') as ff:
                    for linee in ff:
                        token = linee.strip().split()
                        bb = [int(float(token[0])), int(float(token[1])), 
                        int(float(token[2])), int(float(token[3]))]
                        if similarity.iou(gt, bb) < negative_threshold:
                            negative_list.append(bb)

                positive_num = negative_num = 200 #len(negative_list)
                #positive_num = negative_num = 1

                bb = data.proposal_enlarge(im, gt, FLAGS.enlarge)
                positive_path = osp.join(temp_dir, img_name, positive_dir)

                if not os.path.exists(positive_path):
                    os.makedirs(positive_path)

                gt_path = osp.join(positive_path, 'gt.jpg')

                cv2.imwrite(gt_path, positive)
                if FLAGS.da:
                    # remove previous data augmentation
                    try:
                        shutil.rmtree(temp_dir)
                    except:
                        pass
                    
                    positive_list = data.img_augmentation(
                        im[bb[1]:bb[3], bb[0]:bb[2]], positive_num - 1, positive_path)
                    positive_list = os.listdir(positive_path)
                else:
                    positive_list = []
                    for _ in xrange(negative_num):
                        positive_list.append(gt_path)

                
                random.shuffle(positive_list)

                #positive_index = random.sample(xrange(len(positive_list)), positive_num)
                anchor = {}
                for query_dir in query_list:
                    anchor[query_dir] = osp.join(
                        temp_dir, query_dir, query_name + ".jpg")

                for index in random.sample(range(len(negative_list)), negative_num):
                    #IPython.embed()
                    negative_bb = negative_list[index]
                    negative = osp.join(negative_dir, 
                        "{}_{}_{}_{}.jpg".format(
                            negative_bb[0], negative_bb[1], negative_bb[2], negative_bb[3]))
                    
                    train_data.append(TripletData(
                        anchor, positive_list.pop(), negative))
                    sys.stdout.write("\r{:6d}".format(len(train_data)))
                    sys.stdout.flush()

    random.shuffle(train_data)
    print("\nFinish loading... size of training data: {}".format(len(train_data)))
    print(("\nFinish loading... size of training data: {}".format(len(train_data))))
    
    
    # validation on training data
   
    val_ratio = 1./100 # 1./7
    split_index = int(len(train_data) *val_ratio)

    return Dataset(train_data[split_index:]), Dataset(train_data[:split_index])
    

def create_test():
    print("Loading testing data...")
    pair1 = {}
    pair2 = []
    frame_dir = FLAGS.test_dir

    poi_dir   = "poi"
    bb_dir    = "faster_bb"
    query_list = ["search", "streetview_clean", "aerial_clean"]
    query = {}


    for query_dir in query_list:
        query[query_dir] = {}
        for img_name in os.listdir(query_dir):
            gps = img_name.replace(".jpg","").replace(".png","")
            query[query_dir][gps] = cv2.imread(osp.join(query_dir, img_name), cv2.IMREAD_COLOR)




    for img_name in os.listdir(frame_dir):
        im = cv2.imread(osp.join(frame_dir, img_name), cv2.IMREAD_COLOR)
        img_name = img_name.replace(".jpg", "").replace(".png", "")

        with open(osp.join(poi_dir, img_name+".txt"), 'r') as f:
            for line in f:

                token = line.strip().split("\t")
                gps_pos = [0, 0]
                [name, gps_pos[0], gps_pos[1], google_type, img_ref, gt] = token
                str_gps_pos = [gps_pos[0], gps_pos[1]]
                query_name = str(str_gps_pos[0])+'_'+str(str_gps_pos[1])
                

                gt = gt.split(',')
                gt = [int(i) for i in gt]

                positive = im[gt[1]:gt[3], gt[0]:gt[2]]
                anchor = {}
                for query_dir in query_list:
                    if not query_dir in pair1:
                        pair1[query_dir] = []
                    pair1[query_dir].append(data.transform_img(query[query_dir][query_name]))
                pair2.append(data.transform_img(positive))
    for query_dir in query_list:
        pair1[query_dir] = np.array(pair1[query_dir])
    test_data = [pair1, np.array(pair2)]

    print(("\nFinish loading... size of testing data: {}".format(len(test_data[1]))))

    return test_data


def main(argv):
    if FLAGS.isdrone:
        train_data, val_data = create_triplet_drone()
    else: 
        train_data, val_data = create_triplet()
    test_data = create_test()
    if FLAGS.model_dir:
        train(train_data, val_data, test_data, ckptfile=FLAGS.model_dir)
    else:
        train(train_data, val_data, test_data, caffemodel='alexnet_place.npy')
    #train(train_data, test_data, FLAGS.weights, FLAGS.caffemodel)

if __name__ == '__main__':
    main(sys.argv)


#https://www.zhihu.com/question/38937343
