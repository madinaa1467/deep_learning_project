import csv
import io
import json
import os

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

from loguru import logger
from PIL import Image
import ray
import tensorflow as tf

num_images_train = None  #None
num_images_val = None
train_annos_path = './mpii_human_pose_v1_u12_2/train.json'
val_annos_path =  './mpii_human_pose_v1_u12_2/validation.json'

num_train_shards = 64
num_val_shards = 8
ray.init()
tf.get_logger().setLevel('ERROR')

def chunkify(l, n):
    size = len(l) // n
    start = 0
    results = []
    for i in range(n - 1):
        results.append(l[start:start + size])
        start += size
    results.append(l[start:])
    return results


def _bytes_feature(value):
    if isinstance(value, type(tf.constant(0))):
        value = value.numpy(
        )  # BytesList won't unpack a string from an EagerTensor.
    return tf.train.Feature(bytes_list=tf.train.BytesList(value=[value]))

def genreate_tfexample(anno):
    filename = anno['filename']
    filepath = anno['filepath']
    scale = anno['scale']
    center = anno['center']
    with open(filepath, 'rb') as image_file:
        content = image_file.read()

    image = Image.open(filepath)
    if image.format != 'JPEG' or image.mode != 'RGB':
        image_rgb = image.convert('RGB')
        with io.BytesIO() as output:
            image_rgb.save(output, format="JPEG", quality=95)
            content = output.getvalue()

    width, height = image.size
    depth = 3

    x = [
        joint[0] / width if joint[0] >= 0 else joint[0]
        for joint in anno['joints']
    ]
    y = [
        joint[1] / height if joint[1] >= 0 else joint[0]
        for joint in anno['joints']
    ]
    # 0 - invisible, 1 - occluded, 2 - visible
    v = [0 if joint_v == 0 else 2 for joint_v in anno['joints_visibility']]

    feature = {
        'image/height':
            tf.train.Feature(int64_list=tf.train.Int64List(value=[height])),
        'image/width':
            tf.train.Feature(int64_list=tf.train.Int64List(value=[width])),
        'image/depth':
            tf.train.Feature(int64_list=tf.train.Int64List(value=[depth])),
        'image/object/parts/x':
            tf.train.Feature(int64_list=tf.train.Int64List(value=list(map(int, x)))),
        'image/object/parts/y':
            tf.train.Feature(int64_list=tf.train.Int64List(value=list(map(int, y)))),
        'image/object/parts/v':
            tf.train.Feature(int64_list=tf.train.Int64List(value=list(map(int, v)))),
        'image/object/center/x':
            tf.train.Feature(int64_list=tf.train.Int64List(value=[int(center[0])])),
        'image/object/center/y':
            tf.train.Feature(int64_list=tf.train.Int64List(value=[int(center[1])])),
        'image/object/scale':
            tf.train.Feature(float_list=tf.train.FloatList(value=[scale])),
        'image/encoded':
            _bytes_feature(content),
        'image/filename':
            _bytes_feature(filename.encode())
    }

    #     feature = {}
    #     feature['image/height'] = tf.train.Feature(int64_list=tf.train.Int64List(value = [height]))
    #     feature['image/width'] = tf.train.Feature(int64_list=tf.train.Int64List(value = [width]))
    #     feature['image/depth'] = tf.train.Feature(int64_list=tf.train.Int64List(value = [depth]))
    #     feature['image/object/parts/x'] = tf.train.Feature(int64_list=tf.train.Int64List(value = x))
    #     feature['image/object/parts/y'] = tf.train.Feature(int64_list=tf.train.Int64List(value = y))
    #     feature['image/object/parts/v'] = tf.train.Feature(int64_list=tf.train.Int64List(value = v))
    #     feature['image/encoded'] = _bytes_feature(content)
    #     feature['image/filename'] = _bytes_feature(filename.encode())

    features = tf.train.Features(feature=feature)

    return tf.train.Example(features=tf.train.Features(feature=feature))

@ray.remote
def build_single_tfrecord(chunk, path):
    print('start to build tf records for ' + path)

    with tf.io.TFRecordWriter(path) as writer:
        for anno_list in chunk:
            tf_example = genreate_tfexample(anno_list)
            writer.write(tf_example.SerializeToString())

    print('finished building tf records for ' + path)


def build_tf_records(annotations, total_shards, split):
    chunks = chunkify(annotations, total_shards)
    futures = [
        # train_0001_of_0064.tfrecords
        build_single_tfrecord.remote(
            chunk, './tfrecords_mpii/{}_{}_of_{}.tfrecords'.format(
                split,
                str(i + 1).zfill(4),
                str(total_shards).zfill(4),
            )) for i, chunk in enumerate(chunks)
    ]
    ray.get(futures)


def parse_one_annotation(anno, image_dir):
    filename = anno['image']
    joints = anno['joints']
    joints_visibility = anno['joints_vis']
    scale = anno['scale']
    center = anno['center']
    annotation = {
        'filename': filename,
        'filepath': os.path.join(image_dir, filename),
        'joints_visibility': joints_visibility,
        'joints': joints,
        'scale':scale,
        'center':center
    }
    return annotation


# def main():
# if __name__ == '__main__':
#     main()

print('Start to parse annotations.')
if not os.path.exists('./tfrecords_mpii'):
    os.makedirs('./tfrecords_mpii')

with open(train_annos_path) as train_json:
    train_annos = json.load(train_json)
    train_annotations = [
        parse_one_annotation(anno, './mpii/images/')
        for anno in train_annos
    ]
    if num_images_train:
        train_annotations = train_annotations[0:num_images_train]
    print('First train annotation: ', train_annotations[0])
    del (train_annos)

with open(val_annos_path) as val_json:
    val_annos = json.load(val_json)
    val_annotations = [
        parse_one_annotation(anno, './mpii/images/') for anno in val_annos
    ]
    if num_images_val:
        val_annotations = val_annotations[0:num_images_val]
    print('First val annotation: ', val_annotations[0])
    del (val_annos)

print('Start to build TF Records.')
build_tf_records(train_annotations, num_train_shards, 'train')
build_tf_records(val_annotations, num_val_shards, 'val')

print('Successfully wrote {} annotations to TF Records.'.format(
    len(train_annotations) + len(val_annotations)))
