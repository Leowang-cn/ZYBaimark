import { createApp } from 'vue';
import { createRouter, createWebHistory } from 'vue-router';
import App from './App.vue';
import Login from './views/Login.vue';
import Tasks from './views/Tasks.vue';
import NewTask from './views/NewTask.vue';
import TaskDetail from './views/TaskDetail.vue';
import Review from './views/Review.vue';
import Settings from './views/Settings.vue';
import './style.css';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/tasks' },
    { path: '/login', component: Login, meta: { public: true } },
    { path: '/tasks', component: Tasks },
    { path: '/tasks/new', component: NewTask },
    { path: '/tasks/:id', component: TaskDetail, props: true },
    { path: '/videos/:id', component: Review, props: true },
    { path: '/settings', component: Settings },
  ],
});

createApp(App).use(router).mount('#app');
