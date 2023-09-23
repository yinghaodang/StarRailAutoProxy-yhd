import os

import cv2

from basic import os_utils
from basic.img import cv2_utils


class TemplateImage:

    def __init__(self):

        self.origin = None  # 原图 GBRA
        self.gray = None  # 灰度图
        self.mask = None  # 掩码

    def get(self, t: str):
        if t is None or t == 'origin':
            return self.origin
        if t == 'gray':
            return self.gray
        if t == 'mask':
            return self.mask

class ImageHolder:

    def __init__(self):
        self.large_map = {}
        self.template = {}

    def _get_key_for_map(self, planet: str, region: str, map_type: str) -> str:
        return '%s_%s_%s' % (planet, region, map_type)

    def load_large_map(self, planet: str, region: str, map_type: str) -> cv2.typing.MatLike:
        """
        加载某张大地图到内存中
        :param planet: 星球名称
        :param region: 对应区域
        :param map_type: 地图类型
        :return: 地图图片
        """
        file_path = os.path.join(os_utils.get_path_under_work_dir('images', 'map', planet, region), '%s.png' % map_type)
        image = cv2_utils.read_image(file_path)
        if image is not None:
            self.large_map[self._get_key_for_map(planet, region, map_type)] = image
        return image

    def pop_large_map(self, planet: str, region: str, map_type: str):
        """
        将某张地图从内存中删除
        :param planet: 星球名称
        :param region: 对应区域
        :param map_type: 地图类型
        :return:
        """
        key = self._get_key_for_map(planet, region, map_type)
        if key in self.large_map:
            del self.large_map[key]

    def get_large_map(self, planet: str, region: str, map_type: str):
        """
        获取某张大地图
        :param planet: 星球名称
        :param region: 对应区域
        :param map_type: 地图类型
        :return: 地图图片
        """
        key = self._get_key_for_map(planet, region, map_type)
        if key not in self.large_map:
            # 尝试加载一次
            return self.load_large_map(planet, region, map_type)
        else:
            return self.large_map[key]

    def load_template(self, template_id: str) -> cv2.typing.MatLike:
        """
        加载某个模板到内存
        :param template_id: 模板id
        :param template_type: 模板类型
        :return: 模板图片
        """
        dir_path = os.path.join(os_utils.get_path_under_work_dir('images', 'template'), template_id)
        if not os.path.exists(dir_path):
            return None
        template: TemplateImage = TemplateImage()
        template.origin = cv2_utils.read_image(os.path.join(dir_path, 'origin.png'))
        template.gray = cv2_utils.read_image(os.path.join(dir_path, 'gray.png'))
        template.mask = cv2_utils.read_image(os.path.join(dir_path, 'mask.png'))
        self.template[template_id] = template
        return template

    def pop_template(self, template_id: str):
        """
        将某个模板从内存中删除
        :param template_id: 模板id
        :return:
        """
        if template_id in self.template:
            del self.template[template_id]

    def rotate_template(self, template: TemplateImage, rotate_angle: int) -> TemplateImage:
        rotate: TemplateImage = TemplateImage()
        rotate.origin = cv2_utils.image_rotate(template.origin, rotate_angle)
        rotate.gray = cv2_utils.image_rotate(template.gray, rotate_angle)
        rotate.mask = cv2_utils.image_rotate(template.mask, rotate_angle)
        return rotate

    def get_template(self, template_id: str, rotate_angle: int = 0) -> TemplateImage:
        """
        获取某个模板
        :param template_id: 模板id
        :param rotate_angle: 旋转角度 逆时针
        :return: 模板图片
        """
        if rotate_angle == 0:
            if template_id in self.template:
                return self.template[template_id]
            else:
                return self.load_template(template_id)
        else:
            rotate_key = '%s_%d' % (template_id, rotate_angle)
            if rotate_key in self.template:
                return self.template[rotate_key]
            else:
                template = self.get_template(template_id, 0)
                if template is not None:
                    rotate_template = self.rotate_template(template, rotate_angle)
                    self.template[rotate_key] = rotate_template
                    return rotate_template
                else:
                    return None