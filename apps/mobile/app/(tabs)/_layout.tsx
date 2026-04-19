import { Tabs } from 'expo-router';
import { View } from 'react-native';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';

const ACCENT = '#CCFF00';
const INACTIVE = '#71717A';
const TAB_BG = '#0C0C0E';

function TabIcon({ name, library, focused }: { 
  name: string; 
  library: 'Ionicons' | 'MaterialCommunityIcons' | 'FontAwesome5';
  focused: boolean;
}) {
  const color = focused ? '#000000' : INACTIVE;
  const icon = library === 'Ionicons' 
    ? <Ionicons name={name as any} size={24} color={color} />
    : library === 'MaterialCommunityIcons'
    ? <MaterialCommunityIcons name={name as any} size={24} color={color} />
    : <FontAwesome5 name={name as any} size={22} color={color} />;

  return (
    <View style={{
      backgroundColor: focused ? ACCENT : 'transparent',
      borderRadius: 9999,
      width: 40,
      height: 40,
      alignItems: 'center',
      justifyContent: 'center',
    }}>
      {icon}
    </View>
  );
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarShowLabel: false,
        tabBarStyle: {
          backgroundColor: TAB_BG,
          borderTopColor: '#27272A',
          borderTopWidth: 1,
          height: 80,
          paddingBottom: 16,
          paddingTop: 12,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          tabBarIcon: ({ focused }) => (
            <TabIcon name="home-outline" library="Ionicons" focused={focused} />
          ),
        }}
      />
      <Tabs.Screen
        name="sheet"
        options={{
          tabBarIcon: ({ focused }) => (
            <TabIcon name="table-large" library="MaterialCommunityIcons" focused={focused} />
          ),
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          tabBarIcon: ({ focused }) => (
            <TabIcon name="user-circle" library="FontAwesome5" focused={focused} />
          ),
        }}
      />
    </Tabs>
  );
}