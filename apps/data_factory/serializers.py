from rest_framework import serializers
from .models import DataFactoryRecord
from .tool_list import get_tool_list


class DataFactoryRecordSerializer(serializers.ModelSerializer):
    """数据工厂记录序列化器"""
    user_name = serializers.CharField(source='user.username', read_only=True)
    tool_category_display = serializers.CharField(source='get_tool_category_display', read_only=True)
    tool_scenario_display = serializers.CharField(source='get_tool_scenario_display', read_only=True)
    tool_name_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = DataFactoryRecord
        fields = [
            'id', 'user', 'user_name', 'tool_name', 'tool_name_display', 'tool_category', 'tool_category_display',
            'tool_scenario', 'tool_scenario_display', 'input_data', 'output_data',
            'is_saved', 'tags', 'created_at'
        ]
        read_only_fields = ['id', 'user', 'created_at']

    def get_tool_name_display(self, obj):
        """获取工具名称的显示名称"""
        try:
            # 直接在方法内获取工具列表
            tool_list = get_tool_list()
            for tool in tool_list:
                if tool['name'] == obj.tool_name:
                    return tool['display_name']
            return obj.tool_name
        except Exception as e:
            return obj.tool_name


class ToolExecuteSerializer(serializers.Serializer):
    """工具执行序列化器"""
    tool_name = serializers.CharField(required=True)
    tool_category = serializers.CharField(required=True)
    tool_scenario = serializers.CharField(required=True)
    input_data = serializers.JSONField(required=True)
    is_saved = serializers.BooleanField(default=True)
    tags = serializers.ListField(child=serializers.CharField(), required=False, allow_null=True)


class AIFieldDefSerializer(serializers.Serializer):
    """AI 数据生成字段定义"""
    name = serializers.CharField(required=True)
    type = serializers.CharField(required=False, default='string')
    description = serializers.CharField(required=False, default='')
    min = serializers.IntegerField(required=False)
    max = serializers.IntegerField(required=False)
    gender = serializers.ChoiceField(choices=['random', 'male', 'female'], required=False)


class AIClassifyRequestSerializer(serializers.Serializer):
    """字段分类请求"""
    project_id = serializers.IntegerField(required=True)
    api_info = serializers.JSONField(required=True)
    field_defs = AIFieldDefSerializer(many=True, required=True)


class AIGenerateRequestSerializer(serializers.Serializer):
    """AI 数据生成请求"""
    project_id = serializers.IntegerField(required=True)
    field_defs = AIFieldDefSerializer(many=True, required=True)
    api_info = serializers.JSONField(required=False, default=dict)
    user_inputs = serializers.JSONField(required=False, default=dict)
    classification = serializers.JSONField(required=False, default=None)
    count = serializers.IntegerField(required=False, default=5)
    output_format = serializers.ChoiceField(choices=['json', 'sql', 'csv'], required=False, default='json')
    language = serializers.CharField(required=False, default='中文')
