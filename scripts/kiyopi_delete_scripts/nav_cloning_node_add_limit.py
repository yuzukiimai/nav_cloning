#!/usr/bin/env python3 
from __future__ import print_function
import roslib
roslib.load_manifest('nav_cloning')
import rospy
import cv2
from sensor_msgs.msg import Image
from cv_bridge import CvBridge, CvBridgeError
from nav_cloning_add_limit import *
from skimage.transform import resize
from geometry_msgs.msg import Twist
from geometry_msgs.msg import PoseArray
from std_msgs.msg import Int8
from std_srvs.srv import Trigger
from nav_msgs.msg import Path
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_srvs.srv import Empty
from std_srvs.srv import SetBool, SetBoolResponse
import csv
import os
import time
import copy
import sys
import tf
from nav_msgs.msg import Odometry
import random
DURATION = 0.2

class nav_cloning_node:
    def __init__(self):
        rospy.init_node('nav_cloning_node', anonymous=True)
        self.mode = rospy.get_param("/nav_cloning_node/mode", "change_dataset_balance")
        self.action_num = 1
        self.dl = deep_learning(n_action = self.action_num)
        self.bridge = CvBridge()
        self.image_sub = rospy.Subscriber("/camera/rgb/image_raw", Image, self.callback)
        self.image_left_sub = rospy.Subscriber("/camera_left/rgb/image_raw", Image, self.callback_left_camera)
        self.image_right_sub = rospy.Subscriber("/camera_right/rgb/image_raw", Image, self.callback_right_camera)
        self.vel_sub = rospy.Subscriber("/nav_vel", Twist, self.callback_vel)
        self.action_pub = rospy.Publisher("action", Int8, queue_size=1)
        self.nav_pub = rospy.Publisher('/cmd_vel', Twist, queue_size=10)
        self.srv = rospy.Service('/training', SetBool, self.callback_dl_training)
        self.pose_sub = rospy.Subscriber("/amcl_pose", PoseWithCovarianceStamped, self.callback_pose)
        self.path_sub = rospy.Subscriber("/move_base/NavfnROS/plan", Path, self.callback_path)
        self.min_distance = 0.0
        self.action = 0.0
        self.episode = 0
        self.vel = Twist()
        self.path_pose = PoseArray()
        self.cv_image = np.zeros((480,640,3), np.uint8)
        self.cv_left_image = np.zeros((480,640,3), np.uint8)
        self.cv_right_image = np.zeros((480,640,3), np.uint8)
        self.learning = True
        self.select_dl = False
        self.start_time = time.strftime("%Y%m%d_%H:%M:%S")
        self.path = roslib.packages.get_pkg_dir('nav_cloning') + '/data/result_'+str(self.mode)+'/'
        self.save_path = roslib.packages.get_pkg_dir('nav_cloning') + '/data/model_'+str(self.mode)+'/'
        # self.load_path = '/home/kiyooka/catkin_ws/src/nav_cloning/data/analysis/conventional/model.net'
        self.previous_reset_time = 0
        self.pos_x = 0.0
        self.pos_y = 0.0
        self.pos_the = 0.0
        self.is_started = False
        self.start_time_s = rospy.get_time()
        os.makedirs(self.path + self.start_time)
        self.DURATION = 0.2
        # self.limit = [0.09,0.09,0.09,0.09,0.09,0.09,0.09,0.09,0.09,0.09,0.1]
        self.limit = [0.05,0.10,0.15,0.20,0.25,0.25]
        self.dataset_count = [0,0,0,0,0,0]
        self.dataset_count_sum = 0
        self.limit_episode = 8000

        # with open('/home/kiyooka/catkin_ws/src/nav_cloning/data/analysis/conventional/training.csv', 'w') as f:
        #     writer = csv.writer(f, lineterminator='\n')
        #     writer.writerow(['step', 'mode', 'loss', 'angle_error(rad)', 'distance(m)','x(m)','y(m)', 'the(rad)'])
        self.tracker_sub = rospy.Subscriber("/tracker", Odometry, self.callback_tracker)

    def callback(self, data):
        try:
            self.cv_image = self.bridge.imgmsg_to_cv2(data, "bgr8")
        except CvBridgeError as e:
            print(e)

    def callback_left_camera(self, data):
        try:
            self.cv_left_image = self.bridge.imgmsg_to_cv2(data, "bgr8")
        except CvBridgeError as e:
            print(e)

    def callback_right_camera(self, data):
        try:
            self.cv_right_image = self.bridge.imgmsg_to_cv2(data, "bgr8")
        except CvBridgeError as e:
            print(e)

    def callback_tracker(self, data):
        self.pos_x = data.pose.pose.position.x
        self.pos_y = data.pose.pose.position.y
        rot = data.pose.pose.orientation
        angle = tf.transformations.euler_from_quaternion((rot.x, rot.y, rot.z, rot.w))
        self.pos_the = angle[2]

    def callback_path(self, data):
        self.path_pose = data

    def callback_pose(self, data):
        distance_list = []
        pos = data.pose.pose.position
        for pose in self.path_pose.poses:
            path = pose.pose.position
            distance = np.sqrt(abs((pos.x - path.x)**2 + (pos.y - path.y)**2))
            distance_list.append(distance)

        if distance_list:
            self.min_distance = min(distance_list)

    def callback_vel(self, data):
        self.vel = data
        self.action = self.vel.angular.z

    def callback_dl_training(self, data):
        resp = SetBoolResponse()
        self.learning = data.data
        resp.message = "Training: " + str(self.learning)
        resp.success = True
        return resp

    def loop(self):
        if self.cv_image.size != 640 * 480 * 3:
            return
        if self.cv_left_image.size != 640 * 480 * 3:
            return
        if self.cv_right_image.size != 640 * 480 * 3:
            return
        if self.vel.linear.x != 0:
            self.is_started = True
        if self.is_started == False:
            return
        img = resize(self.cv_image, (48, 64), mode='constant')
        r, g, b = cv2.split(img)
        imgobj = np.asanyarray([r,g,b])

        img_left = resize(self.cv_left_image, (48, 64), mode='constant')
        r, g, b = cv2.split(img_left)
        imgobj_left = np.asanyarray([r,g,b])

        img_right = resize(self.cv_right_image, (48, 64), mode='constant')
        r, g, b = cv2.split(img_right)
        imgobj_right = np.asanyarray([r,g,b])

        ros_time = str(rospy.Time.now())

        if self.episode == self.limit_episode:
            self.learning = False
            self.dl.save(self.save_path)
            # self.dl.load(self.load_path)

        if self.episode == self.limit_episode + 2000:
            os.system('killall roslaunch')
            sys.exit()

        if self.learning:
            target_action = self.action
            distance = self.min_distance

            if self.mode == "manual":
                if distance > 0.1:
                    self.select_dl = False
                elif distance < 0.05:
                    self.select_dl = True
                if self.select_dl and self.episode >= 0:
                    target_action = 0
                action, loss = self.dl.act_and_trains(imgobj, target_action)
                if abs(target_action) < 0.1:
                    action_left,  loss_left  = self.dl.act_and_trains(imgobj_left, target_action - 0.2)
                    action_right, loss_right = self.dl.act_and_trains(imgobj_right, target_action + 0.2)
                angle_error = abs(action - target_action)

            elif self.mode == "use_dl_output":
                action, loss = self.dl.act_and_trains(img , target_action)
                if abs(target_action) < 0.1:
                    action_left,  loss_left  = self.dl.act_and_trains(img_left , target_action - 0.2)
                    action_right, loss_right = self.dl.act_and_trains(img_right , target_action + 0.2)


                angle_error = abs(action - target_action)
                if distance > 0.1:
                    self.select_dl = False
                elif distance < 0.05:
                    self.select_dl = True
                if self.select_dl and self.episode >= 0:
                    target_action = action

            elif self.mode == "change_dataset_balance":
                if self.episode <= 100:
                    if distance < 0.02:
                        self.dataset_count[0] += 1 
                        dataset_num = 0
                    elif 0.02 <= distance < 0.04:
                        self.dataset_count[1] += 1 
                        dataset_num = 1
                    elif 0.04 <= distance < 0.06:
                        self.dataset_count[2] += 1 
                        dataset_num = 2
                    elif 0.06 <= distance < 0.08:
                        self.dataset_count[3] += 1 
                        dataset_num = 3
                    elif 0.08 <= distance < 0.10:
                        self.dataset_count[4] += 1 
                        dataset_num = 4
                    elif 0.10 <= distance:
                        self.dataset_count[5] += 1 
                        dataset_num = 5
                    action, loss = self.dl.act_and_trains(img , target_action,dataset_num)
                    if abs(target_action) < 0.1:
                        action_left,  loss_left  = self.dl.act_and_trains(img_left , target_action - 0.2, dataset_num)
                        action_right, loss_right = self.dl.act_and_trains(img_right , target_action + 0.2,dataset_num)


                    self.dataset_count_sum += 1
                    angle_error = abs(action - target_action)
                    line = [str(self.episode), "training", str(distance), str(self.pos_x), str(self.pos_y), str(self.pos_the)]
                    with open(self.path + self.start_time + '/' + 'training.csv', 'a') as f:
                        writer = csv.writer(f, lineterminator='\n')
                        writer.writerow(line)

                else:
                    if distance < 0.02:
                        dataset_num = 0
                        if self.dataset_count[0] / self.episode < self.limit[0]:
                        # if self.dataset_count[0] / self.dataset_count_sum < self.limit[0]:
                            del_flag = False
                            self.dataset_count[0] += 1 
                        else:
                            del_flag = True

                    elif 0.02 <= distance < 0.04:
                        dataset_num = 1
                        if self.dataset_count[1] / self.episode < self.limit[1]:
                        # if self.dataset_count[1] / self.dataset_count_sum < self.limit[1]:
                            del_flag = False
                            self.dataset_count[1] += 1 
                        else:
                            del_flag = True

                    elif 0.04 <= distance < 0.06:
                        dataset_num = 2
                        if self.dataset_count[2] / self.episode < self.limit[2]:
                        # if self.dataset_count[2] / self.dataset_count_sum < self.limit[2]:
                            del_flag = False
                            self.dataset_count[2] += 1 
                        else:
                            del_flag = True

                    elif 0.06 <= distance < 0.08:
                        dataset_num = 3
                        if self.dataset_count[3] / self.episode < self.limit[3]:
                        # if self.dataset_count[3] / self.dataset_count_sum < self.limit[3]:
                            del_flag = False
                            self.dataset_count[3] += 1 
                        else:
                            del_flag = True

                    elif 0.08 <= distance < 0.10:
                        dataset_num = 4
                        if self.dataset_count[4] / self.episode < self.limit[4]:
                        # if self.dataset_count[4] / self.dataset_count_sum < self.limit[4]:
                            del_flag = False
                            self.dataset_count[4] += 1 
                        else:
                            del_flag = True

                    elif 0.10 <= distance:
                        dataset_num = 5
                        if self.dataset_count[5] / self.episode < self.limit[5]:
                        # if self.dataset_count[5] / self.dataset_count_sum < self.limit[5]:
                            del_flag = False
                            self.dataset_count[5] += 1 
                        else:
                            del_flag = True

                    self.dl.make_dataset(img, target_action,dataset_num)
                    self.dataset_count_sum += 1
                    line = [str(self.episode), "training", str(distance), str(self.pos_x), str(self.pos_y), str(self.pos_the)]
                    if del_flag:
                        # if abs(target_action) < 0.1:
                        #     self.dl.delete_dataset(dataset_num)
                        #     self.dataset_count_sum -= 1
                        # else:
                        #     with open(self.path + self.start_time + '/' + 'training.csv', 'a') as f:
                        #         writer = csv.writer(f, lineterminator='\n')
                        #         writer.writerow(line)
                        self.dl.delete_dataset(dataset_num)
                        self.dataset_count_sum -= 1
                        # else:
                        #     with open(self.path + self.start_time + '/' + 'training.csv', 'a') as f:
                        #         writer = csv.writer(f, lineterminator='\n')
                        #         writer.writerow(line)

                    else:
                        with open(self.path + self.start_time + '/' + 'training.csv', 'a') as f:
                            writer = csv.writer(f, lineterminator='\n')
                            writer.writerow(line)
                    loss = self.dl.trains()
                    if abs(target_action) < 0.1:
                        self.dl.make_dataset(img_left , target_action - 0.2,dataset_num )
                        self.dl.make_dataset(img_right , target_action + 0.2,dataset_num)
                        if del_flag:
                            self.dl.delete_dataset(dataset_num)
                            self.dl.delete_dataset(dataset_num)
                        loss = self.dl.trains()
                        loss = self.dl.trains()
                    action = self.dl.act(img)

                print(self.dataset_count)
                print("dataset_sum: " + str(self.dataset_count_sum))
                if distance > 0.1:
                    self.select_dl = False
                elif distance < 0.05:
                    self.select_dl = True
                if self.select_dl and self.episode >= 0:
                    target_action = action


            # end mode

            self.episode += 1
            print(" episode: " + str(self.episode) + ", loss: " + str(loss) + ", distance: " + str(distance))
            # line = [str(self.episode), "training", str(loss), str(angle_error), str(distance), str(self.pos_x), str(self.pos_y), str(self.pos_the)]
            # line = [str(self.episode), "training", str(distance), str(self.pos_x), str(self.pos_y), str(self.pos_the)]
            # with open(self.path + self.start_time + '/' + 'training.csv', 'a') as f:
            #     writer = csv.writer(f, lineterminator='\n')
            #     writer.writerow(line)
            self.vel.linear.x = 0.2
            self.vel.angular.z = target_action
            self.nav_pub.publish(self.vel)

        else:
            target_action = self.dl.act(img)
            distance = self.min_distance
            self.episode += 1
            print("TEST MODE: " + "episode:" + str(self.episode) + ", angular:" + str(target_action) + ", distance: " + str(distance))

            angle_error = abs(self.action - target_action)
            line = [str(self.episode), "test", str(distance), str(self.pos_x), str(self.pos_y), str(self.pos_the)]
            with open(self.path + self.start_time + '/' + 'training.csv', 'a') as f:
                writer = csv.writer(f, lineterminator='\n')
                writer.writerow(line)
            self.vel.linear.x = 0.2
            self.vel.angular.z = target_action
            self.nav_pub.publish(self.vel)

        temp = copy.deepcopy(img)
        cv2.imshow("Resized Image", temp)
        temp = copy.deepcopy(img_left)
        cv2.imshow("Resized Left Image", temp)
        temp = copy.deepcopy(img_right)
        cv2.imshow("Resized Right Image", temp)
        cv2.waitKey(1)
        print("---------------"*5)

if __name__ == '__main__':
    rg = nav_cloning_node()
    r = rospy.Rate(1 / DURATION)
    while not rospy.is_shutdown():
        rg.loop()
        r.sleep()