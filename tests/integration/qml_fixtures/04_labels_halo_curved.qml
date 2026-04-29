<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.5-Prizren" styleCategories="Symbology|Labeling">
  <renderer-v2 type="singleSymbol">
    <symbols>
      <symbol name="0" type="line" alpha="1">
        <layer class="SimpleLine" enabled="1">
          <Option type="Map">
            <Option name="line_color" type="QString" value="120,120,120,255"/>
            <Option name="line_width" type="QString" value="0.6"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style fontFamily="Helvetica" fontSize="10" fontWeight="50" fontItalic="0" textColor="40,40,40,255" textOpacity="1">
        <text-buffer bufferDraw="1" bufferSize="1.2" bufferColor="255,255,255,255" bufferOpacity="0.85" bufferJoinStyle="64"/>
      </text-style>
      <text-format formatNumbers="0" multilineAlign="0" useMaxLineLengthForAutoWrap="1"/>
      <placement placement="3" placementFlags="9" lineAnchorPercent="0.5" lineAnchorType="0"/>
      <rendering scaleVisibility="0" obstacle="1" obstacleType="0" minFeatureSize="0" mergeLines="1"/>
      <fieldName>name</fieldName>
    </settings>
  </labeling>
</qgis>
