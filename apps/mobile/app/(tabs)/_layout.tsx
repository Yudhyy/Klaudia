import { FontAwesome5, Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';
import type { ReactNode } from 'react';
import { StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Colors } from '../../constants/theme';

type TabIconProps = {
  focused: boolean;
  children: (color: string) => ReactNode;
};

function TabIcon({ focused, children }: TabIconProps): React.JSX.Element {
  return (
    <View style={[styles.iconContainer, focused && styles.iconContainerFocused]}>
      {children(focused ? '#000000' : '#71717A')}
    </View>
  );
}

export default function TabsLayout(): React.JSX.Element {
  const insets = useSafeAreaInsets();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarShowLabel: false,
        tabBarStyle: [
          styles.tabBar,
          {
            height: 60 + insets.bottom,
            paddingBottom: Math.max(insets.bottom, 10),
          },
        ],
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          tabBarIcon: ({ focused }) => (
            <TabIcon focused={focused}>
              {(color) => <Ionicons name="chatbubble-outline" size={23} color={color} />}
            </TabIcon>
          ),
        }}
      />
      <Tabs.Screen
        name="sheet"
        options={{
          tabBarIcon: ({ focused }) => (
            <TabIcon focused={focused}>
              {(color) => (
                <MaterialCommunityIcons name="table-large" size={24} color={color} />
              )}
            </TabIcon>
          ),
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          tabBarIcon: ({ focused }) => (
            <TabIcon focused={focused}>
              {(color) => <FontAwesome5 name="user-circle" size={22} color={color} />}
            </TabIcon>
          ),
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
    backgroundColor: Colors.surface,
  },
  iconContainer: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 20,
  },
  iconContainerFocused: {
    backgroundColor: Colors.accent,
  },
});
