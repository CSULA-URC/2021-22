#!/usr/bin/env python

import rospy
import random

from python_qt_binding.QtCore import pyqtSlot
from python_qt_binding.QtCore import Qt
from python_qt_binding.QtCore import Signal
from python_qt_binding.QtGui import QFont
from python_qt_binding.QtWidgets import QApplication
from python_qt_binding.QtWidgets import QHBoxLayout
from python_qt_binding.QtWidgets import QLabel
from python_qt_binding.QtWidgets import QLineEdit
from python_qt_binding.QtWidgets import QPushButton
from python_qt_binding.QtWidgets import QSlider
from python_qt_binding.QtWidgets import QVBoxLayout
from python_qt_binding.QtWidgets import QGridLayout
from python_qt_binding.QtWidgets import QSpinBox
from python_qt_binding.QtWidgets import QWidget

import xml.dom.minidom
from sensor_msgs.msg import JointState
from math import pi
from threading import Thread
import sys
import signal
import math

RANGE = 10000


def get_param(name, value=None):
    private = "~%s" % name
    if rospy.has_param(private):
        return rospy.get_param(private)
    elif rospy.has_param(name):
        return rospy.get_param(name)
    else:
        return value


class JointStatePublisher():
    def __init__(self):
        description = get_param('robot_description')
        robot = xml.dom.minidom.parseString(description).getElementsByTagName('robot')[0]
        self.free_joints = {}
        self.joint_list = []  # for maintaining the original order of the joints
        self.dependent_joints = get_param("dependent_joints", {})
        use_mimic = get_param('use_mimic_tags', True)
        use_small = get_param('use_smallest_joint_limits', True)

        self.zeros = get_param("zeros")

        pub_def_positions = get_param("publish_default_positions", True)
        pub_def_vels = get_param("publish_default_velocities", False)
        pub_def_efforts = get_param("publish_default_efforts", False)

        # Find all non-fixed joints
        for child in robot.childNodes:
            if child.nodeType is child.TEXT_NODE:
                continue
            if child.localName == 'joint':
                jtype = child.getAttribute('type')
                if jtype == 'fixed' or jtype == 'floating':
                    continue
                name = child.getAttribute('name')
                self.joint_list.append(name)
                if jtype == 'continuous':
                    minval = -pi
                    maxval = pi
                else:
                    try:
                        limit = child.getElementsByTagName('limit')[0]
                        minval = float(limit.getAttribute('lower'))
                        maxval = float(limit.getAttribute('upper'))
                    except:
                        rospy.logwarn("%s is not fixed, nor continuous, but limits are not specified!" % name)
                        continue

                safety_tags = child.getElementsByTagName('safety_controller')
                if use_small and len(safety_tags) == 1:
                    tag = safety_tags[0]
                    if tag.hasAttribute('soft_lower_limit'):
                        minval = max(minval, float(tag.getAttribute('soft_lower_limit')))
                    if tag.hasAttribute('soft_upper_limit'):
                        maxval = min(maxval, float(tag.getAttribute('soft_upper_limit')))

                mimic_tags = child.getElementsByTagName('mimic')
                if use_mimic and len(mimic_tags) == 1:
                    tag = mimic_tags[0]
                    entry = {'parent': tag.getAttribute('joint')}
                    if tag.hasAttribute('multiplier'):
                        entry['factor'] = float(tag.getAttribute('multiplier'))
                    if tag.hasAttribute('offset'):
                        entry['offset'] = float(tag.getAttribute('offset'))

                    self.dependent_joints[name] = entry
                    continue

                if name in self.dependent_joints:
                    continue

                if self.zeros and name in self.zeros:
                    zeroval = self.zeros[name]
                elif minval > 0 or maxval < 0:
                    zeroval = (maxval + minval)/2
                else:
                    zeroval = 0

                joint = {'min': minval, 'max': maxval, 'zero': zeroval}
                if pub_def_positions:
                    joint['position'] = zeroval
                if pub_def_vels:
                    joint['velocity'] = 0.0
                if pub_def_efforts:
                    joint['effort'] = 0.0

                if jtype == 'continuous':
                    joint['continuous'] = True
                self.free_joints[name] = joint

        use_gui = get_param("use_gui", False)

        if use_gui:
            num_rows = get_param("num_rows", 0)
            self.app = QApplication(sys.argv)
            self.gui = JointStatePublisherGui("Joint State Publisher", self, num_rows)
            self.gui.show()
        else:
            self.gui = None

        source_list = get_param("source_list", [])
        self.sources = []
        for source in source_list:
            self.sources.append(rospy.Subscriber(source, JointState, self.source_cb))

        self.pub = rospy.Publisher('joint_states', JointState, queue_size=5)

    def source_cb(self, msg):
        for i in range(len(msg.name)):
            name = msg.name[i]
            if name not in self.free_joints:
                continue

            if msg.position:
                position = msg.position[i]
            else:
                position = None
            if msg.velocity:
                velocity = msg.velocity[i]
            else:
                velocity = None
            if msg.effort:
                effort = msg.effort[i]
            else:
                effort = None

            joint = self.free_joints[name]
            if position is not None:
                joint['position'] = position
            if velocity is not None:
                joint['velocity'] = velocity
            if effort is not None:
                joint['effort'] = effort

        if self.gui is not None:
            # signal instead of directly calling the update_sliders method, to switch to the QThread
            self.gui.sliderUpdateTrigger.emit()


  

    def update(self, delta):
        for name, joint in self.free_joints.iteritems():
            forward = joint.get('forward', True)
            if forward:
                joint['position'] += delta
                if joint['position'] > joint['max']:
                    if joint.get('continuous', False):
                        joint['position'] = joint['min']
                    else:
                        joint['position'] = joint['max']
                        joint['forward'] = not forward
            else:
                joint['position'] -= delta
                if joint['position'] < joint['min']:
                    joint['position'] = joint['min']
                    joint['forward'] = not forward


class JointStatePublisherGui(QWidget):
    sliderUpdateTrigger = Signal()

    def __init__(self, title, jsp, num_rows=0):
        super(JointStatePublisherGui, self).__init__()
        self.jsp = jsp
        self.joint_map = {}
        self.vlayout = QVBoxLayout(self)
        self.gridlayout = QGridLayout()
        font = QFont("Helvetica", 9, QFont.Bold)

        ### Generate sliders ###
        sliders = []
        for name in self.jsp.joint_list:
            if name not in self.jsp.free_joints:
                continue
            joint = self.jsp.free_joints[name]

            if joint['min'] == joint['max']:
                continue

            joint_layout = QVBoxLayout()
            row_layout = QHBoxLayout()

            label = QLabel(name)
            label.setFont(font)
            row_layout.addWidget(label)
            display = QLineEdit("0.00")
            display.setAlignment(Qt.AlignRight)
            display.setFont(font)
            display.setReadOnly(True)
            row_layout.addWidget(display)

            joint_layout.addLayout(row_layout)

            slider = QSlider(Qt.Horizontal)

            slider.setFont(font)
            slider.setRange(0, RANGE)
            slider.setValue(RANGE/2)

            joint_layout.addWidget(slider)

            self.joint_map[name] = {'slidervalue': 0, 'display': display,
                                    'slider': slider, 'joint': joint}
            # Connect to the signal provided by QSignal
            slider.valueChanged.connect(self.onValueChanged)
            sliders.append(joint_layout)

        # Determine number of rows to be used in grid
        self.num_rows = num_rows
        # if desired num of rows wasn't set, default behaviour is a vertical layout
        if self.num_rows == 0:
            self.num_rows = len(sliders)  # equals VBoxLayout
        # Generate positions in grid and place sliders there
        self.positions = self.generate_grid_positions(len(sliders), self.num_rows)
        for item, pos in zip(sliders, self.positions):
            self.gridlayout.addLayout(item, *pos)

        # Set zero positions read from parameters
        self.center()

        # Synchronize slider and displayed value
        self.sliderUpdate(None)

        # Set up a signal for updating the sliders based on external joint info
        self.sliderUpdateTrigger.connect(self.updateSliders)

        self.vlayout.addLayout(self.gridlayout)

        # Buttons for randomizing and centering sliders and
        # Spinbox for on-the-fly selecting number of rows
        self.randbutton = QPushButton('Randomize', self)
        self.randbutton.clicked.connect(self.randomize_event)
        self.vlayout.addWidget(self.randbutton)
        self.ctrbutton = QPushButton('Center', self)
        self.ctrbutton.clicked.connect(self.center_event)
        self.vlayout.addWidget(self.ctrbutton)
        self.maxrowsupdown = QSpinBox()
        self.maxrowsupdown.setMinimum(1)
        self.maxrowsupdown.setMaximum(len(sliders))
        self.maxrowsupdown.setValue(self.num_rows)
        self.maxrowsupdown.lineEdit().setReadOnly(True)  # don't edit it by hand to avoid weird resizing of window
        self.maxrowsupdown.valueChanged.connect(self.reorggrid_event)
        self.vlayout.addWidget(self.maxrowsupdown)

    @pyqtSlot(int)
    def onValueChanged(self, event):
        # A slider value was changed, but we need to change the joint_info metadata.
        for name, joint_info in self.joint_map.items():
            joint_info['slidervalue'] = joint_info['slider'].value()
            joint = joint_info['joint']
            joint['position'] = self.sliderToValue(joint_info['slidervalue'], joint)
            joint_info['display'].setText("%.2f" % joint['position'])

    @pyqtSlot()
    def updateSliders(self):
        self.update_sliders()

    def update_sliders(self):
        for name, joint_info in self.joint_map.items():
            joint = joint_info['joint']
            joint_info['slidervalue'] = self.valueToSlider(joint['position'],
                                                           joint)
            joint_info['slider'].setValue(joint_info['slidervalue'])

    def center_event(self, event):
        self.center()

    def center(self):
        rospy.loginfo("Centering")
        for name, joint_info in self.joint_map.items():
            joint = joint_info['joint']
            joint_info['slider'].setValue(self.valueToSlider(joint['zero'], joint))

    def reorggrid_event(self, event):
        self.reorganize_grid(event)

    def reorganize_grid(self, number_of_rows):
        self.num_rows = number_of_rows

        # Remove items from layout (won't destroy them!)
        items = []
        for pos in self.positions:
            item = self.gridlayout.itemAtPosition(*pos)
            items.append(item)
            self.gridlayout.removeItem(item)

        # Generate new positions for sliders and place them in their new spots
        self.positions = self.generate_grid_positions(len(items), self.num_rows)
        for item, pos in zip(items, self.positions):
            self.gridlayout.addLayout(item, *pos)

    def generate_grid_positions(self, num_items, num_rows):
        if num_rows==0:
          return []
        positions = [(y, x) for x in range(int((math.ceil(float(num_items) / num_rows)))) for y in range(num_rows)]
        positions = positions[:num_items]
        return positions

    def randomize_event(self, event):
        self.randomize()

    def randomize(self):
        rospy.loginfo("Randomizing")
        for name, joint_info in self.joint_map.items():
            joint = joint_info['joint']
            joint_info['slider'].setValue(
                    self.valueToSlider(random.uniform(joint['min'], joint['max']), joint))

    def sliderUpdate(self, event):
        for name, joint_info in self.joint_map.items():
            joint_info['slidervalue'] = joint_info['slider'].value()
        self.update_sliders()

    def valueToSlider(self, value, joint):
        return (value - joint['min']) * float(RANGE) / (joint['max'] - joint['min'])

    def sliderToValue(self, slider, joint):
        pctvalue = slider / float(RANGE)
        return joint['min'] + (joint['max']-joint['min']) * pctvalue


if __name__ == '__main__':
    try:
        rospy.init_node('joint_state_publisher')
        jsp = JointStatePublisher()

        if jsp.gui is None:
            jsp.loop()
        else:
            Thread(target=jsp.loop).start()
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            sys.exit(jsp.app.exec_())

    except rospy.ROSInterruptException:
        pass

